"""Part 1: the Credit Watch MCP tool server (MCP Python SDK v2).

REGISTRY declares exactly the closed catalog (Case Study 3B, Appendix C,
the kit's creditwatch/catalog.py); catalog.conformance(REGISTRY) must be [].
build_server(session, audit) returns an MCPServer for ONE session. Every tool
checks its scope on every call with the kit's channel.require_scope (or
require_host) and records a ledger event. On the fixture the READ tools wrap
the kit's panel and signals functions, so results match the notebooks.
"""
import hashlib
import json
import math
import re
from pathlib import Path

from mcp.server import MCPServer

from creditwatch import catalog, channel, clock, memory, panel, signals
from services.actions.engine import Engine
from services.chat.session import ROOT, load_config
from services.mcp import errors
from services.memory.store import build_store
from services.mcp.schemas import (Adsh, ApprovalRequest, BdcName, DraftResult, Filing, Holding,
                                  MandateList, MandateProfile, MandateScope, MemoryHit,
                                  ProposeWriteResult, Scope, SendResult, Signal, WatchlistEntry,
                                  WatchlistWriteResult)

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


def channel_feed(root, P):
    """The channel's recorded messages on the fixture: Priya's corrections (with her
    session token) and the inbox (client and IR email). message_id -> message."""
    fx = Path(root) / P["data"]["fixture_dir"]
    msgs = [json.loads(x) for f in ("corrections.jsonl", "inbox.jsonl")
            for x in (fx / f).read_text(encoding="utf-8").splitlines() if x.strip()]
    return {m["message_id"]: m for m in msgs}


def build_server(session, audit, root=ROOT, panel_df=None, messages=None):
    """One server per session: the session's mandate is the only client in reach."""
    root = Path(root)
    P, users, mandates = load_config(root)
    ws = root / P["agent"]["workspace"]
    memory_cache = root / ".cache" / "memory"              # rebuildable, git-ignored
    feed = messages if messages is not None else channel_feed(root, P)
    pan = panel_df if panel_df is not None else panel.load_panel(root / P["data"]["panel"])
    materiality = P["heartbeat"]["materiality"]
    engine = Engine(P, REGISTRY)                           # policies from params.yaml
    srv = MCPServer("creditwatch-tools")

    def check(tool, scope=None):
        """Runs first on EVERY call: the scope check (kind from the catalog), then the
        policy engine's gate. Returns the tool's policy."""
        if catalog.CATALOG[tool][1] is not None:
            try:
                channel.require_scope(session, scope, catalog.CATALOG[tool][1], audit)
            except channel.ScopeError as e:      # require_scope already logged policy_block
                raise errors.ScopeDenied(str(e)) from e
        return engine.gate(tool, audit, scope)

    def done(tool, scope=None):
        audit.record("tool_call", tool=tool, scope=scope, decision="executed")

    def failed(tool, err, scope=None):
        audit.record("tool_call", tool=tool, scope=scope, decision="failed", error=err.code)
        raise err

    # ---------------------------------------------------------------- READ: session
    @srv.tool()
    def list_mandates() -> MandateList:
        """The mandate ids the logged-in principal serves (READ)."""
        check("list_mandates")
        done("list_mandates")
        return MandateList(principal=session.principal, mandates=users[session.principal]["mandates"])

    @srv.tool()
    def get_filing(adsh: Adsh) -> Filing:
        """One filing's metadata and its SEC link; the link must be an allowed SEC host (READ)."""
        check("get_filing")
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

    @srv.tool()
    def memory_read(scope: MandateScope, query: str) -> list[MemoryHit]:
        """Search the active mandate's own memory store; each hit carries its source
        (READ, scope: the active mandate only)."""
        check("memory_read", scope)
        store = build_store(ws, scope, memory_cache)       # only this mandate's store is ever opened
        try:
            hits = store.search(query)
        finally:
            store.db.close()
        done("memory_read", scope)
        return [MemoryHit(text=t, source=s) for t, s in hits]

    # ---------------------------------------------------------------- INTERNAL_WRITE
    @srv.tool()
    def memory_propose_write(scope: MandateScope, content: str,
                             message_id: str | None = None) -> ProposeWriteResult:
        """Propose a standing rule or memory (INTERNAL_WRITE, scope: the active mandate only).
        Written to PRINCIPAL.md ONLY when message_id is Priya's authenticated message and
        content is exactly what she sent; anything else becomes a proposal for her review."""
        check("memory_propose_write", scope)
        msg = None
        if message_id is not None:
            msg = feed.get(message_id)
            if msg is None:
                failed("memory_propose_write", errors.NotFound(f"no message {message_id!r} on the channel"), scope)
        if msg is None:
            origin = "model"                                # the caller's own idea
        elif channel.authority(msg, users) != "instruction":
            origin = "email"                                # data: client email, IR notes, filings
        elif msg.get("content") != content:
            origin = "model"                                # Priya's id, but words she did not send
        else:
            origin = "principal"
        result = memory.propose_write(scope, content, origin, message_id if origin == "principal" else None,
                                      audit, ws / "PRINCIPAL.md", ws / "proposals", clock.clock().date().isoformat())
        audit.record("tool_call", tool="memory_propose_write", scope=scope, decision=result,
                     message_id=message_id, content_hash=hashlib.sha256(content.encode()).hexdigest())
        return ProposeWriteResult(result=result, origin=origin)

    # ---------------------------------------------------------------- NOT_BUILT stubs
    # Declared now with their names, classes, input schemas and scope checks, so the
    # catalog is complete. Each checks scope FIRST, then raises NotBuilt.
    #   Part 6 (notebook 29): watchlist_write, draft_email, request_approval, send_email
    def not_built(tool, part, scope=None):
        check(tool, scope)
        failed(tool, errors.NotBuilt(f"{tool} arrives in {part}"), scope)

    @srv.tool()
    def watchlist_write(scope: MandateScope, draft_id: str, bdc: str, reason: str,
                        approval_token: str) -> WatchlistWriteResult:
        """Add a watchlist entry approved by Priya for this exact payload (INTERNAL_WRITE,
        approve override; scope: the active mandate only). NOT_BUILT until Part 6."""
        not_built("watchlist_write", "Part 6", scope)

    @srv.tool()
    def draft_email(scope: MandateScope, to: list[str], subject: str, body: str,
                    attachments: list[str] | None = None) -> DraftResult:
        """Draft an email for Priya's approval; nothing is sent (DRAFT, scope: the active
        mandate only). NOT_BUILT until Part 6."""
        not_built("draft_email", "Part 6", scope)

    @srv.tool()
    def request_approval(draft_id: str) -> ApprovalRequest:
        """Ask Priya to approve a stored draft's exact payload (CONTROL). NOT_BUILT until Part 6."""
        not_built("request_approval", "Part 6")

    @srv.tool()
    def send_email(scope: MandateScope, draft_id: str, approval_token: str) -> SendResult:
        """Send an approved draft to the mock outbox only (EXTERNAL_SEND, approve; scope:
        the active mandate only). NOT_BUILT until Part 6."""
        not_built("send_email", "Part 6", scope)

    return srv


def main():
    """Run the server over stdio for ONE session. The token is read ONLY from the
    environment (CREDITWATCH_TOKEN); it is never written to any file or printed.
    Messages go to stderr, because stdout carries the MCP protocol."""
    import os
    import sys

    from creditwatch import audit, clock

    P, users, _ = load_config()
    principal = os.environ.get("CREDITWATCH_PRINCIPAL", P["agent"]["principal"])
    token = os.environ.get("CREDITWATCH_TOKEN")
    mandate = os.environ.get("CREDITWATCH_MANDATE")
    if not token or not mandate:
        print("set CREDITWATCH_TOKEN and CREDITWATCH_MANDATE in the environment", file=sys.stderr)
        sys.exit(2)
    try:
        session = channel.login(principal, token, mandate, users)
    except PermissionError as e:
        print(f"login refused: {e}", file=sys.stderr)
        sys.exit(1)
    del token
    print(f"creditwatch-tools: {session.principal} on {session.mandate} (stdio)", file=sys.stderr)
    build_server(session, audit.Ledger(clock=clock.clock)).run("stdio")


if __name__ == "__main__":
    main()
