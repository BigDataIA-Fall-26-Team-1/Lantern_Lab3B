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

from creditwatch import approvals  # noqa: E402
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
