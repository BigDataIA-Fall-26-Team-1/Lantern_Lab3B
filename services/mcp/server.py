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

from creditwatch import approvals, catalog, channel, clock, memory, panel, policy, signals
from services.actions.engine import Engine
from services.actions.store import ActionStore
from services.chat.session import ROOT, load_config
from services.mcp import errors
from services.memory.store import build_store
from services.mcp.schemas import (Adsh, ApprovalRequest, BdcName, DraftResult, Filing, Holding,
                                  MandateList, MandateProfile, MandateScope, MemoryHit,
                                  ProposeWriteResult, Recipients, Scope, SendResult, Signal,
                                  WatchBdc, WatchlistEntry, WatchlistWriteResult, WatchReason)

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


def others_of(root, P, mandates, active):
    """Every OTHER mandate's names, aliases, confidential terms and canaries."""
    o = policy.confidential_terms(mandates, active)
    canaries = json.loads((Path(root) / P["data"]["fixture_dir"] / "canaries.json").read_text())
    o["terms"] = o["terms"] + [c for m, cs in canaries.items() if m != active for c in cs]
    return o


def build_server(session, audit, root=ROOT, panel_df=None, messages=None, store=None, now=None):
    """One server per session: the session's mandate is the only client in reach.
    `store` holds the session's pending drafts; Priya's grant() reads the same one.
    `now` returns epoch seconds for token expiry (default: the injected LANTERN_CLOCK)."""
    root = Path(root)
    now = now or (lambda: clock.clock().timestamp())
    outbox = root / "outputs" / "outbox"                   # the MOCK outbox: never real email
    P, users, mandates = load_config(root)
    registry = json.loads((root / P["data"]["registry"]).read_text())
    others = others_of(root, P, mandates, session.mandate)
    ws = root / P["agent"]["workspace"]
    memory_cache = root / ".cache" / "memory"              # rebuildable, git-ignored
    feed = messages if messages is not None else channel_feed(root, P)
    store = store if store is not None else ActionStore()
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

    # ---------------------------------------------------------------- DRAFT and CONTROL
    def blocked(tool, rule, why, scope=None, payload=None, reason=None):
        extra = {"payload_hash": approvals.payload_hash(payload)} if payload else {}
        if reason:
            extra["reason"] = reason
        audit.record("policy_block", tool=tool, rule_id=rule, scope=scope, decision="blocked", **extra)
        audit.record("tool_call", tool=tool, scope=scope, decision="blocked", rule_id=rule)
        raise errors.PolicyBlocked(rule, why)

    @srv.tool()
    def draft_email(scope: MandateScope, to: Recipients, subject: str, body: str,
                    attachments: list[str] | None = None, draft_id: str | None = None) -> DraftResult:
        """Draft an email for Priya's approval; nothing is sent (DRAFT, scope: the active
        mandate only). The standing rules run on the draft itself: a draft that breaks one
        is blocked and never stored. Pass draft_id to revise an existing draft."""
        check("draft_email", scope)
        if draft_id is not None:
            old = store.get(draft_id)
            if old is None or "to" not in old or old["scope"] != scope:
                failed("draft_email", errors.NotFound(f"no email draft {draft_id!r} in this session"), scope)
            if draft_id in store.sent:
                blocked("draft_email", "R-SENT", f"{draft_id!r} was already sent", scope)
        payload = {"draft_id": draft_id or store.new_id("d"), "scope": scope, "to": list(to),
                   "subject": subject, "body": body, "attachments": list(attachments or [])}
        rule = policy.policy_check(payload, mandates[scope], registry, others)
        if rule:
            blocked("draft_email", rule, f"the draft breaks standing rule {rule}", scope, payload)
        store.put(payload)
        store.requested.discard(payload["draft_id"])        # a revised draft needs a new request
        h = approvals.payload_hash(payload)
        audit.record("tool_call", tool="draft_email", scope=scope, decision="executed",
                     draft_id=payload["draft_id"], payload_hash=h)
        return DraftResult(draft_id=payload["draft_id"], payload_hash=h)

    @srv.tool()
    def request_approval(draft_id: str) -> ApprovalRequest:
        """Ask Priya to approve a stored draft's exact payload (CONTROL). Returns the payload
        hash she approves; the agent never receives or makes a token."""
        check("request_approval")
        payload = store.get(draft_id)
        if payload is None:
            failed("request_approval", errors.NotFound(f"no pending draft {draft_id!r} in this session"))
        if payload["scope"] != session.mandate:
            raise errors.ScopeDenied(f"draft {draft_id!r} belongs to another mandate")
        h = approvals.payload_hash(payload)
        store.requested.add(draft_id)
        audit.record("approval_requested", tool="request_approval", draft_id=draft_id, payload_hash=h)
        audit.record("tool_call", tool="request_approval", decision="requested", draft_id=draft_id, payload_hash=h)
        return ApprovalRequest(draft_id=draft_id, payload_hash=h, status="requested")

    # ---------------------------------------------------------------- EXTERNAL_SEND
    @srv.tool()
    def send_email(scope: MandateScope, draft_id: str, approval_token: str | None = None) -> SendResult:
        """Send a stored draft to the MOCK outbox (outputs/outbox/) only (EXTERNAL_SEND, approve;
        scope: the active mandate only). Needs Priya's token for the exact stored payload; a
        draft is sent once. The standing rules run again before the send."""
        check("send_email", scope)
        payload = store.get(draft_id)
        if payload is None or "to" not in payload or payload["scope"] != scope:
            failed("send_email", errors.NotFound(f"no email draft {draft_id!r} in this session"), scope)
        if draft_id in store.sent:
            blocked("send_email", "R-SENT", f"{draft_id!r} was already sent", scope, payload)
        if not approval_token:
            blocked("send_email", "R-APPROVAL", "no approval token: request_approval first", scope, payload)
        verdict = approvals.verify_token(payload, approval_token, session.principal, now())
        if verdict != "ok":                                   # payload_changed, expired, wrong_approver, malformed
            blocked("send_email", "R-TOKEN", f"approval token refused: {verdict}", scope, payload, verdict)
        rule = policy.policy_check(payload, mandates[scope], registry, others)
        if rule:
            blocked("send_email", rule, f"the draft breaks standing rule {rule}", scope, payload)
        outbox.mkdir(parents=True, exist_ok=True)
        message_id = f"mock-{len(list(outbox.glob('mock-*.json'))) + 1:04d}"
        h = approvals.payload_hash(payload)
        (outbox / f"{message_id}.json").write_text(json.dumps(
            {"message_id": message_id, "draft_id": draft_id, "payload_hash": h, "to": payload["to"],
             "subject": payload["subject"], "body": payload["body"], "attachments": payload["attachments"]},
            indent=2), encoding="utf-8")
        store.sent.add(draft_id)
        audit.record("tool_call", tool="send_email", scope=scope, decision="executed",
                     draft_id=draft_id, message_id=message_id, payload_hash=h)
        return SendResult(message_id=message_id, status="sent")

    # ---------------------------------------------------------------- INTERNAL_WRITE, approve override
    @srv.tool()
    def watchlist_write(scope: MandateScope, draft_id: str, bdc: WatchBdc, reason: WatchReason,
                        approval_token: str | None = None) -> WatchlistWriteResult:
        """Add an entry to the active client's watchlist (INTERNAL_WRITE with an approve override:
        clients rely on it; scope: the active mandate only). Without a token the entry is stored
        and status is 'requested': call request_approval. With Priya's token for exactly
        {draft_id, scope, bdc, reason}, the line '- BDC: reason (added YYYY-MM-DD)' is written."""
        check("watchlist_write", scope)
        payload = {"draft_id": draft_id, "scope": scope, "bdc": bdc, "reason": reason}
        if draft_id in store.sent:
            blocked("watchlist_write", "R-SENT", f"{draft_id!r} was already written", scope, payload)
        text = f"{bdc} {reason}"
        if policy.mentions(text, others["names"]) or policy.leaks(text, others["terms"]):
            blocked("watchlist_write", "R-OTHER-MANDATE", "the entry names another client", scope, payload)
        if policy.mentions(text, registry["restricted"]):
            blocked("watchlist_write", "R-RESTRICTED", "the entry names a restricted issuer", scope, payload)
        h = approvals.payload_hash(payload)
        if not approval_token:                                 # dev-06: requested, never executed
            store.put(payload)
            store.requested.discard(draft_id)
            audit.record("tool_call", tool="watchlist_write", scope=scope, decision="requested",
                         draft_id=draft_id, payload_hash=h)
            return WatchlistWriteResult(entry_id=None, status="requested")
        verdict = approvals.verify_token(payload, approval_token, session.principal, now())
        if verdict != "ok":                                    # e.g. the reason was edited after approval
            blocked("watchlist_write", "R-TOKEN", f"approval token refused: {verdict}", scope, payload, verdict)
        path = ws / "mandates" / scope / "WATCHLIST.md"
        lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip() != "(no entries)"]
        line = f"- {bdc}: {reason} (added {clock.clock().date().isoformat()})"
        if not WATCH_LINE.match(line):                         # always readable by watchlist_read
            blocked("watchlist_write", "R-FORMAT", "the entry would not parse as a watchlist line", scope, payload)
        path.write_text("\n".join([*lines, line]) + "\n", encoding="utf-8")
        store.sent.add(draft_id)
        entry_id = f"wl-{h[:12]}"
        audit.record("tool_call", tool="watchlist_write", scope=scope, decision="executed",
                     draft_id=draft_id, entry_id=entry_id, payload_hash=h)
        return WatchlistWriteResult(entry_id=entry_id, status="written")

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
