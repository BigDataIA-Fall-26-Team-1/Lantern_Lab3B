"""Part 4 tests: governed memory and mandate isolation.

Every test runs on a scratch copy of the project (scratch_root, conftest.py).
Run from the repo root:  PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q
"""
import asyncio
import json

from mcp import Client

from creditwatch import audit, channel, clock, evals, memory, policy
from services.chat.session import load_config
from services.mcp.server import build_server

TOKEN = "priya-fixture-token"
B_WORDS = ["allocation code Quillwort letter", "Northgate", "NG-PC-9902", "Quillwort 8830"]


def server_for(root, mandate):
    _, users, _ = load_config(root)
    led = audit.Ledger(clock=clock.clock)
    return build_server(channel.login("priya", TOKEN, mandate, users), led, root=root), led


def call(server, tool, args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args)
    return asyncio.run(go())


def hits(server, mandate, query):
    r = call(server, "memory_read", {"scope": mandate, "query": query})
    assert not r.is_error, r.content
    return r.structured_content["result"]


def error_text(r):
    assert r.is_error
    return " ".join(x.text for x in r.content)


def others(root, active):
    """The OTHER mandate's names, aliases, confidential terms and canaries."""
    P, _, mandates = load_config(root)
    o = policy.confidential_terms(mandates, active)
    canaries = json.loads((root / P["data"]["fixture_dir"] / "canaries.json").read_text())
    o["canaries"] = [c for m, cs in canaries.items() if m != active for c in cs]
    return o


def leaks(texts, o):
    t = "\n".join(texts)
    return policy.mentions(t, o["names"]) + policy.leaks(t, o["terms"]) + [c for _, c in evals.canary_hits([t], o["canaries"])]


# ---- 1: the memory_read contract
def test_contract_memory_read(scratch_root):
    server, led = server_for(scratch_root, "mandate_a")

    async def schema():
        async with Client(server) as c:
            return next(t for t in (await c.list_tools()).tools if t.name == "memory_read").input_schema
    s = asyncio.run(schema())
    assert set(s["properties"]) == {"scope", "query"} and set(s["required"]) == {"scope", "query"}

    found = hits(server, "mandate_a", "Golf PIK")
    assert found == [{"text": "Harbor State asked for the Golf PIK history before the quarterly call.",
                      "source": "log/2026-10-01.md#1"}]
    assert led.find("tool_call", tool="memory_read", decision="executed")
    for scope in ("mandate_b", "public"):                     # mandate scope only
        assert "SCOPE_DENIED" in error_text(call(server, "memory_read", {"scope": scope, "query": "Hotel"}))
    assert len(led.find("policy_block", rule_id="R-SCOPE")) == 2
    assert "validation error" in error_text(call(server, "memory_read", {"scope": "mandate_a"}))


# ---- 2: canary tests over memory reads
def test_memory_reads_never_return_the_other_mandates_content(scratch_root):
    a, _ = server_for(scratch_root, "mandate_a")
    for q in B_WORDS:                                         # A searches for B's own words: nothing
        assert hits(a, "mandate_a", q) == []
    every_a_hit = [h["text"] for q in ["Golf", "Harbor", "PIK", "quarterly", "Hotel", "client"]
                   for h in hits(a, "mandate_a", q)]
    assert every_a_hit and leaks(every_a_hit, others(scratch_root, "mandate_a")) == []

    b, _ = server_for(scratch_root, "mandate_b")              # the search works: B finds its own line
    own = hits(b, "mandate_b", "allocation code Quillwort letter")
    assert own and own[0]["source"] == "log/2026-10-01.md#1"
    assert evals.canary_hits([own[0]["text"]], ["NGM-Quillwort-8830"])       # its own hidden canary
    every_b_hit = [h["text"] for q in ["Hotel", "allocation", "letter", "Golf", "Harbor"] for h in hits(b, "mandate_b", q)]
    assert leaks(every_b_hit, others(scratch_root, "mandate_b")) == []


# ---- 3: isolation is physical, one store per mandate
def test_a_session_opens_only_its_own_store(scratch_root):
    a, _ = server_for(scratch_root, "mandate_a")
    hits(a, "mandate_a", "Golf")
    cache = scratch_root / ".cache" / "memory"
    assert [p.name for p in cache.iterdir()] == ["mandate_a"]
    assert (cache / "mandate_a" / "memory.sqlite").exists()


# ---- 8 (read half): cross-session recall; isolation does not come from forgetting
def test_a_fact_from_one_session_is_recalled_in_the_next_and_never_in_the_other(scratch_root):
    ws = scratch_root / "agent" / "mandates"
    s1, _ = server_for(scratch_root, "mandate_a")             # session 1: an observation is logged
    assert hits(s1, "mandate_a", "Crestline") == []
    ref = memory.append_log(ws, "mandate_a", "2026-10-15", "Juliett 10-Q: Crestline price fell from 97.5 to 90.0")

    s2, _ = server_for(scratch_root, "mandate_a")             # session 2: a NEW session recalls it
    assert hits(s2, "mandate_a", "Crestline") == [
        {"text": "Juliett 10-Q: Crestline price fell from 97.5 to 90.0", "source": ref}]

    b, _ = server_for(scratch_root, "mandate_b")              # the other client never sees it
    assert hits(b, "mandate_b", "Crestline") == []
