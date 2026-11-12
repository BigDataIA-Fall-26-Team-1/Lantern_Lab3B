"""Part 1: the Credit Watch MCP tool server (MCP Python SDK v2).

REGISTRY declares exactly the closed catalog (Case Study 3B, Appendix C,
the kit's creditwatch/catalog.py); catalog.conformance(REGISTRY) must be [].
build_server(session, audit) returns an MCPServer for ONE session. Every tool
checks its scope on every call with the kit's channel.require_scope (or
require_host) and records a ledger event. On the fixture the READ tools wrap
the kit's panel and signals functions, so results match the notebooks.
"""
import math
import re
from pathlib import Path

from mcp.server import MCPServer

from creditwatch import catalog, channel, panel, signals
from services.chat.session import ROOT, load_config
from services.mcp import errors
from services.mcp.schemas import (Adsh, BdcName, Filing, Holding, MandateList, MandateProfile,
                                  MandateScope, Scope, Signal, WatchlistEntry)

REGISTRY = {                      # tool -> action class; the closed catalog, nothing else
    "list_mandates": "READ",
    "bdc_holdings": "READ",
    "bdc_signals": "READ",
    "get_filing": "READ",
    "mandate_profile": "READ",
    "watchlist_read": "READ",
    "memory_read": "READ",
    "memory_propose_write": "INTERNAL_WRITE",
    "watchlist_write": "INTERNAL_WRITE",
    "draft_email": "DRAFT",
    "request_approval": "CONTROL",
    "send_email": "EXTERNAL_SEND",
}

WATCH_LINE = re.compile(r"^- (?P<bdc>.+?): (?P<reason>.+) \(added (?P<added>\d{4}-\d{2}-\d{2})\)$")


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def build_server(session, audit, root=ROOT, panel_df=None):
    """One server per session: the session's mandate is the only client in reach."""
    root = Path(root)
    P, users, mandates = load_config(root)
    ws = root / P["agent"]["workspace"]
    pan = panel_df if panel_df is not None else panel.load_panel(root / P["data"]["panel"])
    materiality = P["heartbeat"]["materiality"]
    srv = MCPServer("creditwatch-tools")

    def check(tool, scope):
        """The scope kind comes from the catalog; the check runs on EVERY call."""
        try:
            channel.require_scope(session, scope, catalog.CATALOG[tool][1], audit)
        except channel.ScopeError as e:          # require_scope already logged policy_block
            raise errors.ScopeDenied(str(e)) from e

    def done(tool, scope=None):
        audit.record("tool_call", tool=tool, scope=scope, decision="executed")

    def failed(tool, err, scope=None):
        audit.record("tool_call", tool=tool, scope=scope, decision="failed", error=err.code)
        raise err

    # ---------------------------------------------------------------- READ: session
    @srv.tool()
    def list_mandates() -> MandateList:
        """The mandate ids the logged-in principal serves (READ)."""
        done("list_mandates")
        return MandateList(principal=session.principal, mandates=users[session.principal]["mandates"])

    @srv.tool()
    def get_filing(adsh: Adsh) -> Filing:
        """One filing's metadata and its SEC link; the link must be an allowed SEC host (READ)."""
        rows = pan[pan["adsh"] == adsh]
        if rows.empty:
            failed("get_filing", errors.NotFound(f"no filing {adsh!r} in the panel"))
        r = rows.iloc[0]
        try:
            channel.require_host(r["inlineurl"], audit)
        except PermissionError as e:             # require_host already logged policy_block
            raise errors.HostDenied(str(e)) from e
        done("get_filing")
        return Filing(adsh=adsh, cik=str(r["cik"]), name=r["name"], form=r["form"],
                      period=str(r["period"]), filed=str(r["filed"]), url=r["inlineurl"])

    # ---------------------------------------------------------------- READ: public
    @srv.tool()
    def bdc_holdings(scope: Scope, bdc: BdcName = None) -> list[Holding]:
        """Current-period holdings from the two-period panel (READ, scope public)."""
        check("bdc_holdings", scope)
        cur = pan[pan["period_role"] == "current"]
        if bdc is not None:
            cur = cur[cur["name"] == bdc]
            if cur.empty:
                failed("bdc_holdings", errors.NotFound(f"no BDC named {bdc!r} in the panel"), scope)
        done("bdc_holdings", scope)
        return [Holding(adsh=h.adsh, name=h.name, identifier=h.identifier, fair_value=_num(h.fair_value),
                        principal=_num(h.principal), pik_rate=_num(h.pik_rate), source_rows=str(h.source_rows))
                for h in cur.itertuples()]

    @srv.tool()
    def bdc_signals(scope: Scope) -> list[Signal]:
        """Within-filing credit signals with their evidence: filing, current row, prior row (READ, scope public)."""
        check("bdc_signals", scope)
        found = signals.find_signals(pan, materiality)        # Part 5 passes our own pairing here
        done("bdc_signals", scope)
        return [Signal(**{k: (str(v) if k in ("row", "prior_row") else v) for k, v in s.items()})
                for s in found.to_dict("records")]

    # ---------------------------------------------------------------- READ: mandate
    @srv.tool()
    def mandate_profile(scope: MandateScope) -> MandateProfile:
        """The active client's name and positions (READ, scope: the active mandate only)."""
        check("mandate_profile", scope)
        m = mandates[scope]
        done("mandate_profile", scope)
        return MandateProfile(mandate=scope, client=m["client"], positions=m["positions"])

    @srv.tool()
    def watchlist_read(scope: MandateScope) -> list[WatchlistEntry]:
        """The active client's watchlist, from its WATCHLIST.md (READ, scope: the active mandate only)."""
        check("watchlist_read", scope)
        text = (ws / "mandates" / scope / "WATCHLIST.md").read_text(encoding="utf-8")
        done("watchlist_read", scope)
        return [WatchlistEntry(**m.groupdict()) for m in map(WATCH_LINE.match, text.splitlines()) if m]

    return srv
