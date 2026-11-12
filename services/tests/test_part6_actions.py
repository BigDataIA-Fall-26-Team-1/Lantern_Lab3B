"""Part 6 tests: the policy engine, approvals bound to the exact payload, every
blocked case, and proof that an approved send reaches the mock outbox.

Every test that writes runs on a scratch copy (scratch_root, conftest.py).
Run from the repo root:  PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q
"""
import asyncio

import pytest
import yaml
from mcp import Client

from creditwatch import audit, catalog, channel, clock
from services.actions.engine import Engine
from services.chat.session import ROOT, load_config
from services.mcp.server import REGISTRY, build_server

TOKEN = "priya-fixture-token"


def server_for(root, mandate="mandate_a"):
    _, users, _ = load_config(root)
    led = audit.Ledger(clock=clock.clock)
    return build_server(channel.login("priya", TOKEN, mandate, users), led, root=root), led


def call(server, tool, args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})
    return asyncio.run(go())


def error_text(r):
    assert r.is_error, r.structured_content
    return " ".join(x.text for x in r.content)


def set_actions(root, **changes):
    """Edit the SCRATCH copy's params.yaml actions section."""
    p = root / "params.yaml"
    P = yaml.safe_load(p.read_text())
    for k, v in changes.items():
        P["actions"][k] = {**(P["actions"].get(k) or {}), **v}
    p.write_text(yaml.safe_dump(P))


# ---- the policy engine reads params.yaml
def test_params_policies_match_the_kit_catalog_for_every_tool():
    P, _, _ = load_config(ROOT)
    engine = Engine(P, REGISTRY)
    assert {t: engine.policy_for(t) for t in REGISTRY} == {t: catalog.policy_for(t) for t in REGISTRY}
    assert engine.policy_for("watchlist_write") == "approve"            # the override, from params.yaml


@pytest.mark.parametrize("change,tool,args", [
    ({"overrides": {"bdc_signals": "forbidden"}}, "bdc_signals", {"scope": "public"}),     # one tool
    ({"policy": {"READ": "forbidden"}}, "mandate_profile", {"scope": "mandate_a"}),         # a whole class
    ({"policy": {"READ": "forbidden"}}, "get_filing", {"adsh": "0009000014-26-000141"}),   # a no-scope tool
])
def test_changing_params_yaml_changes_what_the_server_allows(scratch_root, change, tool, args):
    server, _ = server_for(scratch_root)
    assert not call(server, tool, args).is_error                         # allowed as shipped
    set_actions(scratch_root, **change)
    server, led = server_for(scratch_root)
    text = error_text(call(server, tool, args))
    assert "POLICY_BLOCKED" in text and "R-FORBIDDEN" in text
    assert led.find("policy_block", tool=tool, rule_id="R-FORBIDDEN", decision="blocked")


def test_approve_on_a_tool_without_a_token_is_refused(scratch_root):
    set_actions(scratch_root, overrides={"bdc_holdings": "approve"})
    server, led = server_for(scratch_root)
    assert "R-APPROVAL" in error_text(call(server, "bdc_holdings", {"scope": "public"}))
    assert led.find("policy_block", tool="bdc_holdings", rule_id="R-APPROVAL")


def test_notify_records_an_event_without_content(scratch_root):
    server, led = server_for(scratch_root)
    content = "Lesson: never mention another client."
    call(server, "memory_propose_write", {"scope": "mandate_a", "content": content})
    events = led.find("notify", tool="memory_propose_write", scope="mandate_a")
    assert len(events) == 1 and set(events[0]) == {"event_id", "ts", "kind", "tool", "scope"}
    assert not led.find("notify", tool="bdc_signals")                    # READ is auto: no notify


def test_an_invalid_params_policy_refuses_to_build(scratch_root):
    set_actions(scratch_root, policy={"READ": "sometimes"})
    with pytest.raises(ValueError, match="sometimes"):
        server_for(scratch_root)


# ---- draft_email: the standing rules run on the draft (tutorial slide 35, notebook 29 cell 2)
import json  # noqa: E402

from creditwatch import approvals, policy  # noqa: E402
from services.actions.approve import ApprovalRefused, grant  # noqa: E402
from services.actions.store import ActionStore  # noqa: E402

BODY = "Juliett marked Crestline Roofing down 7.5 points, to 90.0, in its June 10-Q (0009000014-26-000141)."
REPLY = {"scope": "mandate_a", "to": ["cio@harborstate.example"], "subject": "Juliett after its 10-Q", "body": BODY}


def session_server(root, mandate="mandate_a", store=None):
    """A server plus the session's ActionStore and ledger, so Priya's grant() shares them."""
    _, users, _ = load_config(root)
    s = channel.login("priya", TOKEN, mandate, users)
    led, store = audit.Ledger(clock=clock.clock), store or ActionStore()
    return build_server(s, led, root=root, store=store), led, store, s


def ok(r):
    assert not r.is_error, r.content
    return r.structured_content


def test_contract_draft_email_stores_a_clean_reply(scratch_root):
    server, led, store, _ = session_server(scratch_root)
    d = ok(call(server, "draft_email", REPLY))
    stored = store.get(d["draft_id"])
    assert d["draft_id"] == "d-0001" and stored == {**REPLY, "draft_id": "d-0001", "attachments": []}
    assert d["payload_hash"] == approvals.payload_hash(stored)
    assert led.find("tool_call", tool="draft_email", decision="executed", payload_hash=d["payload_hash"])
    assert BODY not in json.dumps(led.events)                                # hashes and ids only


@pytest.mark.parametrize("name,change,rule", [
    ("answers the question about Northgate", {"body": BODY + " Northgate has been adding to Hotel."}, "R-OTHER-MANDATE"),
    ("obfuscated account code of the other client", {"body": BODY + " Ref NG​-PC 9902."}, "R-OTHER-MANDATE"),
    ("the other client's hidden canary", {"body": BODY + " NGM-Quillwort 8830"}, "R-OTHER-MANDATE"),
    ("names a restricted issuer", {"body": BODY + " Ironwood Data Systems is refinancing."}, "R-RESTRICTED"),
    ("writes to the borrower", {"to": ["cfo@crestlineroofing.example"]}, "R-BORROWER"),
    ("copies an unknown address", {"to": ["cio@harborstate.example", "me@gmail.example"]}, "R-NOT-CONTACT"),
])
def test_a_draft_that_breaks_a_standing_rule_is_blocked_and_never_stored(scratch_root, name, change, rule):
    server, led, store, _ = session_server(scratch_root)
    text = error_text(call(server, "draft_email", {**REPLY, **change}))
    assert "POLICY_BLOCKED" in text and rule in text, name
    assert led.find("policy_block", tool="draft_email", rule_id=rule, decision="blocked")
    assert store.pending == {}                                               # never stored: nothing to approve
    assert change.get("body", "@@") not in json.dumps(led.events)


def test_revising_a_draft_changes_its_payload_hash(scratch_root):
    server, _, store, _ = session_server(scratch_root)
    d1 = ok(call(server, "draft_email", REPLY))
    d2 = ok(call(server, "draft_email", {**REPLY, "draft_id": d1["draft_id"],
                                          "to": ["cio@harborstate.example", "credit.pm@harborstate.example"]}))
    assert d2["draft_id"] == d1["draft_id"] and d2["payload_hash"] != d1["payload_hash"]
    assert store.get(d1["draft_id"])["to"] == ["cio@harborstate.example", "credit.pm@harborstate.example"]
    assert "NOT_FOUND" in error_text(call(server, "draft_email", {**REPLY, "draft_id": "d-0999"}))


def test_draft_email_refuses_another_scope_and_bad_input(scratch_root):
    server, led, store, _ = session_server(scratch_root)
    for scope in ("mandate_b", "public"):
        assert "SCOPE_DENIED" in error_text(call(server, "draft_email", {**REPLY, "scope": scope}))
    assert "validation error" in error_text(call(server, "draft_email", {**REPLY, "to": []}))     # no recipient
    assert "validation error" in error_text(call(server, "draft_email", {k: v for k, v in REPLY.items() if k != "body"}))
    assert store.pending == {}


# ---- request_approval asks; only Priya's grant() makes a token
def test_contract_request_approval_asks_and_never_returns_a_token(scratch_root):
    server, led, store, _ = session_server(scratch_root)
    d = ok(call(server, "draft_email", REPLY))
    r = ok(call(server, "request_approval", {"draft_id": d["draft_id"]}))
    assert r == {"draft_id": d["draft_id"], "payload_hash": d["payload_hash"], "status": "requested"}
    assert led.find("approval_requested", draft_id=d["draft_id"], payload_hash=d["payload_hash"])
    assert led.find("tool_call", tool="request_approval", decision="requested")
    assert "NOT_FOUND" in error_text(call(server, "request_approval", {"draft_id": "d-0999"}))
    assert "validation error" in error_text(call(server, "request_approval", {}))


def test_priya_grants_a_token_for_the_exact_stored_payload(scratch_root):
    P, _, _ = load_config(scratch_root)
    server, led, store, s = session_server(scratch_root)
    d = ok(call(server, "draft_email", REPLY))
    now = clock.clock().timestamp()
    with pytest.raises(ApprovalRefused, match="no approval request"):           # a request comes first
        grant(store, d["draft_id"], s, led, now, P["approvals"]["ttl_minutes"])
    ok(call(server, "request_approval", {"draft_id": d["draft_id"]}))
    tok = grant(store, d["draft_id"], s, led, now, P["approvals"]["ttl_minutes"])
    assert approvals.verify_token(store.get(d["draft_id"]), tok, "priya", now + 60) == "ok"
    assert led.find("approval_granted", approver="priya", draft_id=d["draft_id"], payload_hash=d["payload_hash"])
    assert tok not in json.dumps(led.events)                                     # the token is not logged

    _, users, _ = load_config(scratch_root)
    other = channel.login("priya", TOKEN, "mandate_b", users)                    # another mandate's session
    with pytest.raises(ApprovalRefused, match="another mandate"):
        grant(store, d["draft_id"], other, led, now, P["approvals"]["ttl_minutes"])
    with pytest.raises(ApprovalRefused, match="no pending draft"):
        grant(store, "d-0999", s, led, now, P["approvals"]["ttl_minutes"])


# ---- send_email: an approved send reaches the MOCK outbox; everything else is blocked
class Clock:
    """Injected time, so 'thirty-one minutes later' needs no waiting."""
    def __init__(self):
        self.t = clock.clock().timestamp()

    def __call__(self):
        return self.t


def approved(root, mandate="mandate_a", reply=REPLY):
    """Draft, request, and Priya's grant. Returns everything a send needs."""
    P, users, _ = load_config(root)
    s = channel.login("priya", TOKEN, mandate, users)
    led, store, t = audit.Ledger(clock=clock.clock), ActionStore(), Clock()
    server = build_server(s, led, root=root, store=store, now=t)
    d = ok(call(server, "draft_email", reply))
    ok(call(server, "request_approval", {"draft_id": d["draft_id"]}))
    tok = grant(store, d["draft_id"], s, led, t(), P["approvals"]["ttl_minutes"])
    return server, led, store, t, d, tok


def outbox_files(root):
    return sorted((root / "outputs" / "outbox").glob("*.json"))


def send(server, d, tok, scope="mandate_a"):
    return call(server, "send_email", {"scope": scope, "draft_id": d["draft_id"], "approval_token": tok})


def test_an_approved_send_reaches_the_mock_outbox(scratch_root):
    server, led, store, t, d, tok = approved(scratch_root)
    t.t += 120                                                                   # sent two minutes later
    assert ok(send(server, d, tok)) == {"message_id": "mock-0001", "status": "sent"}

    [f] = outbox_files(scratch_root)
    msg = json.loads(f.read_text())
    assert f.name == "mock-0001.json"
    assert (msg["to"], msg["subject"], msg["body"]) == (REPLY["to"], REPLY["subject"], BODY)
    assert msg["payload_hash"] == d["payload_hash"]

    story = [(e["kind"], e.get("tool"), e.get("decision")) for e in led.events
             if e["kind"] in ("tool_call", "approval_requested", "approval_granted")]
    assert story == [("tool_call", "draft_email", "executed"),
                     ("approval_requested", "request_approval", None),
                     ("tool_call", "request_approval", "requested"),
                     ("approval_granted", None, None),
                     ("tool_call", "send_email", "executed")]
    assert led.find("tool_call", tool="send_email", message_id="mock-0001", payload_hash=d["payload_hash"])
    assert BODY not in json.dumps(led.events) and tok not in json.dumps(led.events)     # ids and hashes only

    from services.mcp.server import others_of                                     # nothing of the other client
    P, _, mandates = load_config(scratch_root)
    o = others_of(scratch_root, P, mandates, "mandate_a")
    assert policy.mentions(f.read_text(), o["names"]) == [] and policy.leaks(f.read_text(), o["terms"]) == []


def _no_token(server, led, store, t, d, tok):
    return send(server, d, None), "R-APPROVAL", None


def _garbage_token(server, led, store, t, d, tok):
    return send(server, d, "not-a-token"), "R-TOKEN", "malformed"


def _recipient_added_after_approval(server, led, store, t, d, tok):         # Exercise 29B, question 1
    ok(call(server, "draft_email", {**REPLY, "draft_id": d["draft_id"],
                                     "to": REPLY["to"] + ["credit.pm@harborstate.example"]}))
    return send(server, d, tok), "R-TOKEN", "payload_changed"


def _thirty_one_minutes_later(server, led, store, t, d, tok):
    t.t += 31 * 60
    return send(server, d, tok), "R-TOKEN", "expired"


def _token_issued_for_tom(server, led, store, t, d, tok):
    tom = approvals.issue_token(store.get(d["draft_id"]), "tom", now=t(), ttl_min=30)
    return send(server, d, tom), "R-TOKEN", "wrong_approver"


@pytest.mark.parametrize("case", [_no_token, _garbage_token, _recipient_added_after_approval,
                                  _thirty_one_minutes_later, _token_issued_for_tom])
def test_a_send_without_a_valid_token_for_the_exact_payload_is_blocked(scratch_root, case):
    server, led, store, t, d, tok = approved(scratch_root)
    r, rule, reason = case(server, led, store, t, d, tok)
    text = error_text(r)
    assert "POLICY_BLOCKED" in text and rule in text and (reason is None or reason in text)
    block = led.find("policy_block", tool="send_email", rule_id=rule, decision="blocked")
    assert block and (reason is None or block[-1]["reason"] == reason)
    assert led.find("tool_call", tool="send_email", decision="blocked")      # dev-05's expected decision
    assert outbox_files(scratch_root) == []                                   # nothing reached the outbox
    assert not led.find("tool_call", tool="send_email", decision="executed")


def test_a_draft_is_sent_once(scratch_root):
    server, led, store, t, d, tok = approved(scratch_root)
    ok(send(server, d, tok))
    text = error_text(send(server, d, tok))                                   # the same token, again
    assert "R-SENT" in text and len(outbox_files(scratch_root)) == 1
    assert "R-SENT" in error_text(call(server, "draft_email", {**REPLY, "draft_id": d["draft_id"]}))


def test_send_email_refuses_an_unknown_draft_and_another_scope(scratch_root):
    server, led, store, t, d, tok = approved(scratch_root)
    assert "NOT_FOUND" in error_text(call(server, "send_email", {"scope": "mandate_a", "draft_id": "d-0999",
                                                                 "approval_token": tok}))
    for scope in ("mandate_b", "public"):
        assert "SCOPE_DENIED" in error_text(send(server, d, tok, scope=scope))
    assert "validation error" in error_text(call(server, "send_email", {"scope": "mandate_a"}))
    assert outbox_files(scratch_root) == []


# ---- watchlist_write: approval for the exact entry, then a line watchlist_read parses back
HOTEL = "FIXTURE HOTEL SENIOR LENDING"
REASON = "Brightwater down 5.5 points; Oakmont PIK up from 1.00% to 3.00% (0009000012-26-000121)"


def entry(**change):
    return {"scope": "mandate_b", "draft_id": "w-001", "bdc": HOTEL, "reason": REASON, **change}


def requested_entry(root, mandate="mandate_b", **change):
    """watchlist_write without a token (requested), request_approval, and Priya's grant."""
    P, users, _ = load_config(root)
    s = channel.login("priya", TOKEN, mandate, users)
    led, store, t = audit.Ledger(clock=clock.clock), ActionStore(), Clock()
    server = build_server(s, led, root=root, store=store, now=t)
    r = ok(call(server, "watchlist_write", entry(scope=mandate, **change)))
    assert r == {"entry_id": None, "status": "requested"}
    ok(call(server, "request_approval", {"draft_id": "w-001"}))
    tok = grant(store, "w-001", s, led, t(), P["approvals"]["ttl_minutes"])
    return server, led, store, tok


def watchlist_text(root, mandate):
    return (root / "agent" / "mandates" / mandate / "WATCHLIST.md").read_text()


def test_without_a_token_the_entry_is_requested_and_nothing_is_written(scratch_root):
    server, led, store, _ = session_server(scratch_root, "mandate_b")
    before = watchlist_text(scratch_root, "mandate_b")
    assert ok(call(server, "watchlist_write", entry())) == {"entry_id": None, "status": "requested"}
    assert watchlist_text(scratch_root, "mandate_b") == before
    assert store.get("w-001") == {"draft_id": "w-001", "scope": "mandate_b", "bdc": HOTEL, "reason": REASON}
    assert led.find("tool_call", tool="watchlist_write", decision="requested")     # dev-06's expected decision
    assert not led.find("tool_call", tool="watchlist_write", decision="executed")


def test_an_approved_entry_is_written_in_the_exact_format_and_read_back(scratch_root):
    """The user's requirement: watchlist_write writes '- BDC: reason (added YYYY-MM-DD)',
    exactly what watchlist_read parses, and the written entry reads back."""
    from services.mcp.server import WATCH_LINE
    server, led, store, tok = requested_entry(scratch_root)
    r = ok(call(server, "watchlist_write", entry(approval_token=tok)))
    assert r["status"] == "written" and r["entry_id"].startswith("wl-")

    line = f"- {HOTEL}: {REASON} (added 2026-10-15)"
    lines = watchlist_text(scratch_root, "mandate_b").splitlines()
    assert lines == ["# WATCHLIST: mandate_b", "", line]          # the "(no entries)" placeholder is gone
    assert WATCH_LINE.match(line)
    back = ok(call(server, "watchlist_read", {"scope": "mandate_b"}))["result"]
    assert back == [{"bdc": HOTEL, "reason": REASON, "added": "2026-10-15"}]
    assert led.find("tool_call", tool="watchlist_write", decision="executed", entry_id=r["entry_id"])
    assert REASON not in json.dumps(led.events)                                    # ids and hashes only


def test_an_entry_is_appended_after_existing_entries(scratch_root):
    server, _, _, tok = requested_entry(scratch_root, "mandate_a", bdc="FIXTURE JULIETT LENDING BDC",
                                        reason="Crestline down 7.5 points (0009000014-26-000141)")
    ok(call(server, "watchlist_write", entry(scope="mandate_a", bdc="FIXTURE JULIETT LENDING BDC",
                                             reason="Crestline down 7.5 points (0009000014-26-000141)",
                                             approval_token=tok)))
    back = ok(call(server, "watchlist_read", {"scope": "mandate_a"}))["result"]
    assert [(w["bdc"], w["added"]) for w in back] == [("FIXTURE GOLF CAPITAL CORP", "2026-07-14"),
                                                     ("FIXTURE JULIETT LENDING BDC", "2026-10-15")]


def test_an_entry_edited_after_approval_is_blocked(scratch_root):
    server, led, _, tok = requested_entry(scratch_root)
    before = watchlist_text(scratch_root, "mandate_b")
    text = error_text(call(server, "watchlist_write", entry(reason="fine", approval_token=tok)))
    assert "R-TOKEN" in text and "payload_changed" in text
    assert led.find("policy_block", tool="watchlist_write", rule_id="R-TOKEN", reason="payload_changed")
    assert watchlist_text(scratch_root, "mandate_b") == before


@pytest.mark.parametrize("change,rule", [
    ({"reason": "Harbor State is adding to Hotel as well"}, "R-OTHER-MANDATE"),        # the other client's name
    ({"reason": "see HS​-CR 4471"}, "R-OTHER-MANDATE"),                         # its code, obfuscated
    ({"reason": "exposure to Ironwood Data Systems"}, "R-RESTRICTED"),
])
def test_an_entry_that_breaks_a_standing_rule_is_blocked_before_it_is_stored(scratch_root, change, rule):
    server, led, store, _ = session_server(scratch_root, "mandate_b")
    assert rule in error_text(call(server, "watchlist_write", entry(**change)))
    assert store.pending == {} and led.find("policy_block", tool="watchlist_write", rule_id=rule)


def test_an_entry_is_written_once_and_keeps_its_format(scratch_root):
    server, _, _, tok = requested_entry(scratch_root)
    ok(call(server, "watchlist_write", entry(approval_token=tok)))
    assert "R-SENT" in error_text(call(server, "watchlist_write", entry(approval_token=tok)))
    assert watchlist_text(scratch_root, "mandate_b").count(HOTEL) == 1
    for bad in ({"bdc": "HOTEL: AND MORE"}, {"reason": "two\nlines"}):           # would break the line format
        assert "validation error" in error_text(call(server, "watchlist_write", entry(draft_id="w-002", **bad)))
    for scope in ("mandate_a", "public"):                                         # a mandate_b session
        assert "SCOPE_DENIED" in error_text(call(server, "watchlist_write", entry(scope=scope)))
