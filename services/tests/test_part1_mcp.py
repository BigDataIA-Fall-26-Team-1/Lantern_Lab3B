"""Part 1 contract tests: the closed catalog, and one contract test per tool.

Each tool is called through the SDK's in-memory Client(server): no transport,
no subprocess (tutorial slide 17). Run from the repo root:
    PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q
"""
import asyncio
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mcp import Client  # noqa: E402

from creditwatch import audit, catalog, channel, clock, panel  # noqa: E402
from services.chat.session import load_config  # noqa: E402
from services.mcp import schemas  # noqa: E402
from services.mcp.server import REGISTRY, build_server  # noqa: E402

P, USERS, MANDATES = load_config(ROOT)
TRUTH = pd.read_csv(ROOT / P["data"]["fixture_dir"] / "ground_truth.csv", dtype=str)
BUILT = ["list_mandates", "bdc_holdings", "bdc_signals", "get_filing", "mandate_profile", "watchlist_read",
         "memory_read", "memory_propose_write",   # memory tools: contract tests in test_part4_memory.py
         "draft_email", "request_approval", "send_email", "watchlist_write"]   # actions: test_part6_actions.py


@pytest.fixture
def srv():
    """A server for Priya's mandate_a session, with an in-memory ledger."""
    s = channel.login("priya", "priya-fixture-token", "mandate_a", USERS)
    led = audit.Ledger(clock=clock.clock)
    return build_server(s, led), led


def call(server, tool, args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})
    return asyncio.run(go())


def tools(server):
    async def go():
        async with Client(server) as c:
            return {t.name: t for t in (await c.list_tools()).tools}
    return asyncio.run(go())


def error_text(r):
    assert r.is_error, r.structured_content
    return " ".join(x.text for x in r.content)


def assert_schema(tool, required, optional=()):
    props = tool.input_schema["properties"]
    assert set(props) == set(required) | set(optional)
    assert set(tool.input_schema.get("required", [])) == set(required)


def assert_scope_denied(server, led, tool, args):
    before = len(led.find("policy_block", rule_id="R-SCOPE"))
    assert "SCOPE_DENIED" in error_text(call(server, tool, args))
    assert len(led.find("policy_block", rule_id="R-SCOPE")) == before + 1


# ---- the closed catalog
def test_registry_matches_the_closed_catalog():
    assert catalog.conformance(REGISTRY) == []


def test_server_registers_only_catalog_tools(srv):
    server, _ = srv
    names = set(tools(server))
    assert names == set(REGISTRY)             # exactly the 12: no tool outside the catalog, none missing
    assert set(BUILT) == set(REGISTRY)


# ---- one contract test per tool
def test_contract_list_mandates(srv):
    server, led = srv
    t = tools(server)["list_mandates"]
    assert_schema(t, required=[])
    r = call(server, "list_mandates")
    out = schemas.MandateList(**r.structured_content)
    assert out.principal == "priya" and out.mandates == USERS["priya"]["mandates"]
    assert led.find("tool_call", tool="list_mandates", decision="executed")


def test_contract_bdc_holdings(srv):
    server, led = srv
    assert_schema(tools(server)["bdc_holdings"], required=["scope"], optional=["bdc"])
    pan = panel.load_panel(ROOT / P["data"]["panel"])
    cur = pan[pan["period_role"] == "current"]
    for scope in ("public", "mandate_a"):     # public tools accept public or the active mandate
        rows = [schemas.Holding(**h) for h in call(server, "bdc_holdings", {"scope": scope}).structured_content["result"]]
        assert len(rows) == len(cur)
    hotel = call(server, "bdc_holdings", {"scope": "public", "bdc": "FIXTURE HOTEL SENIOR LENDING"}).structured_content["result"]
    assert hotel and {h["name"] for h in hotel} == {"FIXTURE HOTEL SENIOR LENDING"}
    assert "NOT_FOUND" in error_text(call(server, "bdc_holdings", {"scope": "public", "bdc": "NO SUCH BDC"}))
    assert_scope_denied(server, led, "bdc_holdings", {"scope": "mandate_b"})
    assert "validation error" in error_text(call(server, "bdc_holdings", {}))


def test_contract_bdc_signals(srv):
    server, led = srv
    assert_schema(tools(server)["bdc_signals"], required=["scope"])
    sig = [schemas.Signal(**s) for s in call(server, "bdc_signals", {"scope": "public"}).structured_content["result"]]
    assert len(sig) == len(TRUTH)             # our pairing (Part 5) finds every planted event
    assert all(s.adsh and s.row and s.prior_row for s in sig)      # every signal carries its evidence
    crest = next(s for s in sig if "Crestline" in s.identifier)
    assert (crest.rule, crest.before, crest.after, crest.row, crest.prior_row) == ("MARKDOWN", 97.5, 90.0, "103", "503")
    assert_scope_denied(server, led, "bdc_signals", {"scope": "mandate_b"})
    assert "validation error" in error_text(call(server, "bdc_signals", {"scope": 7}))


def test_contract_get_filing(srv):
    server, led = srv
    assert_schema(tools(server)["get_filing"], required=["adsh"])
    f = schemas.Filing(**call(server, "get_filing", {"adsh": "0009000014-26-000141"}).structured_content)
    assert f.name == "FIXTURE JULIETT LENDING BDC" and f.form == "10-Q" and f.url.startswith("https://www.sec.gov/")
    assert "NOT_FOUND" in error_text(call(server, "get_filing", {"adsh": "0009999999-26-000999"}))
    assert "validation error" in error_text(call(server, "get_filing", {"adsh": "not-an-accession"}))


def test_contract_get_filing_refuses_a_non_sec_host():
    s = channel.login("priya", "priya-fixture-token", "mandate_a", USERS)
    led = audit.Ledger(clock=clock.clock)
    pan = panel.load_panel(ROOT / P["data"]["panel"])
    pan.loc[pan["adsh"] == "0009000011-26-000111", "inlineurl"] = "https://example.com/exfil?x=1"
    r = call(build_server(s, led, panel_df=pan), "get_filing", {"adsh": "0009000011-26-000111"})
    assert "HOST_DENIED" in error_text(r)
    assert led.find("policy_block", rule_id="R-HOST", host="example.com")


def test_contract_mandate_profile(srv):
    server, led = srv
    assert_schema(tools(server)["mandate_profile"], required=["scope"])
    prof = schemas.MandateProfile(**call(server, "mandate_profile", {"scope": "mandate_a"}).structured_content)
    assert prof.client == MANDATES["mandate_a"]["client"] and prof.positions == MANDATES["mandate_a"]["positions"]
    assert_scope_denied(server, led, "mandate_profile", {"scope": "mandate_b"})   # the other client
    assert_scope_denied(server, led, "mandate_profile", {"scope": "public"})      # mandate data is never public
    assert "validation error" in error_text(call(server, "mandate_profile", {}))


def test_contract_watchlist_read(srv):
    server, led = srv
    assert_schema(tools(server)["watchlist_read"], required=["scope"])
    wl = [schemas.WatchlistEntry(**w) for w in call(server, "watchlist_read", {"scope": "mandate_a"}).structured_content["result"]]
    assert [(w.bdc, w.added) for w in wl] == [("FIXTURE GOLF CAPITAL CORP", "2026-07-14")]
    assert_scope_denied(server, led, "watchlist_read", {"scope": "mandate_b"})
    assert_scope_denied(server, led, "watchlist_read", {"scope": "public"})
    assert "validation error" in error_text(call(server, "watchlist_read", {"scope": None}))


# ---- every catalog tool is built: no NOT_BUILT stub may ever come back
def test_no_not_built_stub_is_left():
    for f in ("server.py", "errors.py"):
        src = (ROOT / "services" / "mcp" / f).read_text()
        assert "NOT_BUILT" not in src and "NotBuilt" not in src and "not_built" not in src, f


# ---- the __main__ stdio entry: the token comes only from the environment
def _env(**extra):
    return {"PYTHONPATH": "src", **extra}


def test_stdio_entry_serves_one_session_from_environment_token():
    from mcp import StdioServerParameters
    params = StdioServerParameters(command=sys.executable, args=["-m", "services.mcp.server"], cwd=str(ROOT),
                                   env=_env(CREDITWATCH_TOKEN="priya-fixture-token", CREDITWATCH_MANDATE="mandate_a"))

    async def go():
        async with Client(params) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            sig = await c.call_tool("bdc_signals", {"scope": "public"})
            other = await c.call_tool("mandate_profile", {"scope": "mandate_b"})
            return names, sig, other
    names, sig, other = asyncio.run(go())
    assert names == set(REGISTRY)
    assert len(sig.structured_content["result"]) == len(TRUTH)
    assert "SCOPE_DENIED" in error_text(other)


@pytest.mark.parametrize("env,code,message", [
    (_env(CREDITWATCH_TOKEN="guessed", CREDITWATCH_MANDATE="mandate_a"), 1, "login refused: bad credentials"),
    (_env(CREDITWATCH_MANDATE="mandate_a"), 2, "set CREDITWATCH_TOKEN"),
])
def test_stdio_entry_refuses_without_a_good_token(env, code, message):
    import subprocess
    r = subprocess.run([sys.executable, "-m", "services.mcp.server"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert r.returncode == code and message in r.stderr
    assert r.stdout == "" and "guessed" not in r.stderr          # nothing on the protocol channel; token never echoed
