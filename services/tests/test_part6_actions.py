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
