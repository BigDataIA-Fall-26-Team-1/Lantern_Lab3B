"""Part 2 tests: the real model behind the kit's ReplayClient, with routing and budgets.

Steps 1 and 2 use a FAKE provider (no network, no real key). The tests that need the real
recorded cache (evals/replay/) skip with a clear message until the record step has been run.
Run from the repo root:  PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q
"""
import json
import os

import httpx
import pytest

from creditwatch import clock, loop
from creditwatch.llm import CacheMiss
from services.chat.session import ROOT, load_config
from services.llm import model as llm
from services.llm import openai_provider

P, USERS, MANDATES = load_config(ROOT)
CFG = P["model"]
FAKE_KEY = "sk-test-FAKEKEY0123456789abcdef"


def completion(model, tool=None, args=None, text=None, call_id="call_1", t_in=1000, t_out=100):
    msg = {"role": "assistant", "content": text}
    if tool:
        msg["tool_calls"] = [{"id": call_id, "type": "function",
                              "function": {"name": tool, "arguments": json.dumps(args or {})}}]
    return {"id": "chatcmpl-x", "model": model, "choices": [{"message": msg}],
            "usage": {"prompt_tokens": t_in, "completion_tokens": t_out}}


class FakeChat:
    """Stands in for openai_provider.chat; counts calls and keeps the bodies it was sent."""

    def __init__(self, reply):
        self.reply, self.bodies = reply, []

    def __call__(self, body, base_url):
        self.bodies.append(json.loads(json.dumps(body)))
        return self.reply(body, len(self.bodies))


def scan(paths, needles):
    return [(str(p), n) for p in paths for n in needles if n in p.read_text()]


# ---- 1: record once, replay exactly, never call the provider in replay
def test_record_then_replay_is_exact_and_an_unrecorded_request_is_a_cache_miss(tmp_path):
    fake = FakeChat(lambda b, n: completion(b["model"], text="hello"))
    req = llm.body(CFG, CFG["routes"]["triage"], [{"role": "user", "content": "hi"}])
    rec = llm.client(CFG, tmp_path, "record", chat=fake)(req)
    assert rec["text"] == "hello" and rec["model"] == "gpt-5.4-nano" and len(fake.bodies) == 1
    assert rec["usd"] == llm.price(CFG, "gpt-5.4-nano", 1000, 100) == round((1000 * 0.20 + 100 * 1.25) / 1e6, 8)
    rep = llm.client(CFG, tmp_path, "replay", chat=fake)(req)
    assert rep == rec and len(fake.bodies) == 1                         # replay: no provider call
    with pytest.raises(CacheMiss):
        llm.client(CFG, tmp_path, "replay", chat=fake)({**req, "messages": [{"role": "user", "content": "new"}]})
    assert len(fake.bodies) == 1
    assert "temperature" not in req                                     # omitted for GPT-5 models


# ---- 2: the key never lands in a body, the cache, or an error message
def test_the_key_goes_only_into_the_authorization_header(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, headers=headers)
        return httpx.Response(200, json=completion("gpt-5.4-mini", text="ok"), request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    openai_provider.chat({"model": "gpt-5.4-mini", "messages": []}, CFG["base_url"])
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in json.dumps(seen["body"])


def test_errors_never_carry_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    def boom(url, **kw):
        raise httpx.ConnectError(f"could not connect using {FAKE_KEY}")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(openai_provider.ProviderError) as e:
        openai_provider.chat({"model": "m"}, CFG["base_url"])
    assert FAKE_KEY not in str(e.value) and "[REDACTED]" in str(e.value)

    def denied(url, **kw):
        return httpx.Response(401, text=f'{{"error": "Incorrect API key provided: {FAKE_KEY}"}}',
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", denied)
    with pytest.raises(openai_provider.ProviderError) as e:
        openai_provider.chat({"model": "m"}, CFG["base_url"])
    assert "HTTP 401" in str(e.value) and FAKE_KEY not in str(e.value) and "sk-test" not in str(e.value)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(openai_provider.ProviderError, match="not set"):
        openai_provider.chat({"model": "m"}, CFG["base_url"])


def test_a_recorded_cache_holds_no_key_and_no_auth_material(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    fake = FakeChat(lambda b, n: completion(b["model"], tool="bdc_signals", args={"scope": "public", "rationale": "r"}))
    c = llm.client(CFG, tmp_path, "record", chat=fake)
    for i in range(3):
        c(llm.body(CFG, "gpt-5.4-mini", [{"role": "user", "content": f"task {i}"}]))
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 3
    assert scan(files, [FAKE_KEY, "sk-", "Authorization", "Bearer"]) == []
    cached = json.loads(files[0].read_text())
    assert set(cached) <= {"model", "tokens_in", "tokens_out", "usd", "tool", "args", "rationale", "call_id",
                           "arguments_raw", "text"}


# ---- the agent model rebuilds the conversation exactly, so replays send identical requests
def test_the_agent_model_rebuilds_the_conversation_with_tool_call_ids(tmp_path):
    def reply(b, n):
        return completion(b["model"], tool="bdc_signals", args={"scope": "public", "rationale": "what changed"},
                          call_id=f"call_{n}") if n == 1 else completion(b["model"], text="done")
    fake = FakeChat(reply)
    m = llm.AgentModel(CFG, llm.client(CFG, tmp_path, "record", chat=fake), "gpt-5.4-mini", tools=[])
    text, trace = loop.run_agent(m, {"bdc_signals": lambda scope: [{"rule": "MARKDOWN"}]}, "task", clock.clock)
    assert text == "done" and trace[0]["tool"] == "bdc_signals" and trace[0]["rationale"] == "what changed"
    second = fake.bodies[1]["messages"]
    assert second[2]["tool_calls"][0]["id"] == "call_1" and second[3] == {
        "role": "tool", "tool_call_id": "call_1", "content": json.dumps([{"rule": "MARKDOWN"}])}
    assert fake.bodies[0]["parallel_tool_calls"] is False

    again = llm.AgentModel(CFG, llm.client(CFG, tmp_path, "replay"), "gpt-5.4-mini", tools=[])
    text2, trace2 = loop.run_agent(again, {"bdc_signals": lambda scope: [{"rule": "MARKDOWN"}]}, "task", clock.clock)
    assert (text2, json.dumps(trace2, sort_keys=True)) == (text, json.dumps(trace, sort_keys=True))
    assert len(fake.bodies) == 2                                       # the replay made no provider call


# ---- 5: the budget guard stops cleanly with real token counts, and the record session has a hard cap
def _looping(tmp_path, t_in=20000, t_out=1000):
    fake = FakeChat(lambda b, n: completion(b["model"], tool="bdc_signals", args={"scope": "public", "rationale": "again"},
                                            call_id=f"call_{n}", t_in=t_in, t_out=t_out))
    return llm.AgentModel(CFG, llm.client(CFG, tmp_path, "record", chat=fake), "gpt-5.4-mini", tools=[])


def test_the_budget_guard_stops_a_run_cleanly(tmp_path):
    tools = {"bdc_signals": lambda scope: []}
    _, t = loop.run_agent(_looping(tmp_path / "a"), tools, "loop", clock.clock, usd_cap=0.03)
    assert t[-1]["stop"] == "budget_usd" and sum(r["usd"] for r in t) > 0.03
    _, t = loop.run_agent(_looping(tmp_path / "b", t_in=30000), tools, "loop", clock.clock, token_cap=60000)
    assert t[-1]["stop"] == "budget_tokens"
    _, t = loop.run_agent(_looping(tmp_path / "c", t_in=10, t_out=1), tools, "loop", clock.clock, max_steps=4)
    assert t[-1]["stop"] == "max_steps"


def test_the_record_session_stops_before_passing_its_cap(tmp_path):
    spend = llm.Spend(cap=0.05)
    fake = FakeChat(lambda b, n: completion(b["model"], text="x", t_in=30000, t_out=1000))   # $0.027 a call
    c = llm.client(CFG, tmp_path, "record", spend=spend, chat=fake)
    c(llm.body(CFG, "gpt-5.4-mini", [{"role": "user", "content": "1"}]))
    c(llm.body(CFG, "gpt-5.4-mini", [{"role": "user", "content": "2"}]))
    with pytest.raises(llm.RecordBudgetExceeded, match="record budget reached"):
        c(llm.body(CFG, "gpt-5.4-mini", [{"role": "user", "content": "3"}]))
    assert len(fake.bodies) == 2 and len(list(tmp_path.glob("*.json"))) == 2    # what was recorded is kept
    c(llm.body(CFG, "gpt-5.4-mini", [{"role": "user", "content": "1"}]))         # a cache hit costs nothing
    assert len(fake.bodies) == 2


# ---- step 2: routing, triage, the runner on the real-model path (with a fake OpenAI)
import re  # noqa: E402

from services.evals import cli  # noqa: E402
from services.evals.runner import run_in, run_task  # noqa: E402

TOKEN = "priya-fixture-token"
DEV = {t["task_id"]: t for t in cli.load_tasks(ROOT, "dev")}


def fake_openai(b, n):
    """A tiny scripted 'OpenAI': triage by keywords; the agent reads its context and calls tools."""
    user = next(m["content"] for m in b["messages"] if m["role"] == "user")
    if "response_format" in b:                                       # triage
        kind = ("isolation" if "Northgate" in user else "heartbeat" if "release tick" in user
                else "client_note")
        return completion(b["model"], text=json.dumps({"kind": kind, "skill": "client-monthly-note"}), t_in=300, t_out=20)
    mandate = re.search(r"# MANDATE: (mandate_\w)", user).group(1)
    done = sum(m["role"] == "tool" for m in b["messages"])
    plan = [("mandate_profile", {"scope": mandate}), ("bdc_signals", {"scope": "public"})]
    if done < len(plan):
        tool, args = plan[done]
        return completion(b["model"], tool=tool, args={**args, "rationale": f"step {done + 1}"}, call_id=f"call_{n}")
    return completion(b["model"], text="Your exposure: the client's own BDCs only.")


def test_triage_and_materiality_go_to_the_small_model_and_the_agent_to_the_main_one(scratch_root, tmp_path):
    fake = FakeChat(fake_openai)
    note, ex = run_in(scratch_root, DEV["dev-01"], TOKEN, provider="openai", mode="record", cache_dir=tmp_path, chat=fake)
    assert note["models"] == {"triage": "gpt-5.4-nano", "loop": "gpt-5.4-mini", "route": "agent"}
    assert note["kind"] == "client_note" and note["skill"] == "client-monthly-note"
    assert "## skill: client-monthly-note" in ex["text"]                 # triage opened ONE skill card
    beat, _ = run_in(scratch_root, DEV["dev-07"], TOKEN, provider="openai", mode="record", cache_dir=tmp_path, chat=fake)
    assert beat["models"] == {"triage": "gpt-5.4-nano", "loop": "gpt-5.4-nano", "route": "materiality"}
    sent = [(("triage" if "response_format" in b else "loop"), b["model"]) for b in fake.bodies]
    assert ("triage", "gpt-5.4-nano") in sent and ("loop", "gpt-5.4-mini") in sent
    assert all(m == "gpt-5.4-nano" for k, m in sent[-3:])               # dev-07: every call on the small model
    cached = {json.loads(p.read_text())["model"] for p in tmp_path.glob("*.json")}
    assert cached == {"gpt-5.4-mini", "gpt-5.4-nano"}
    assert [f"{a['tool']} {a['decision']}" for a in note["actions"]] == ["mandate_profile executed", "bdc_signals executed"]
    assert all(r["rationale"] for r in ex["trace"] if r.get("tool"))   # every step states its reason


def test_a_task_replayed_twice_from_the_cache_is_identical_and_calls_nothing(scratch_root, tmp_path):
    run_task(scratch_root, DEV["dev-01"], TOKEN, provider="openai", mode="record", cache_dir=tmp_path,
             chat=FakeChat(fake_openai))

    def never(body, base_url):
        raise AssertionError("replay must not call the provider")
    a = run_task(scratch_root, DEV["dev-01"], TOKEN, provider="openai", cache_dir=tmp_path, chat=never)
    b = run_task(scratch_root, DEV["dev-01"], TOKEN, provider="openai", cache_dir=tmp_path, chat=never)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True) and a["trace_hash"] == b["trace_hash"]
    assert a["passed"] and a["usd"] > 0


def test_replay_without_a_recording_says_so_clearly(scratch_root, monkeypatch, tmp_path):
    monkeypatch.setenv("CREDITWATCH_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "run_task", lambda *a, **k: run_task(*a, **{**k, "cache_dir": tmp_path}))
    printed = []
    assert cli.main(["eval", "run", "--provider", "openai", "--suite", "dev"], root=scratch_root, out=printed.append) == 1
    assert printed[-1].startswith("not recorded yet")


def test_the_estimate_calls_nothing_and_record_needs_the_key(scratch_root, monkeypatch):
    monkeypatch.setenv("CREDITWATCH_TOKEN", TOKEN)
    monkeypatch.setattr(openai_provider, "chat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no API call")))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    printed = []
    assert cli.main(["eval", "run", "--provider", "openai", "--suite", "dev", "--mode", "record", "--estimate"], root=scratch_root,
                    out=printed.append) == 0
    total = printed[-1]
    assert total.startswith("ESTIMATE") and "No API call was made" in total and len(printed) == 9
    usd = float(re.search(r"~\$([0-9.]+) \(hard cap", total).group(1))
    assert 0 < usd < CFG["record_budget_usd"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert cli.main(["eval", "run", "--provider", "openai", "--suite", "dev", "--mode", "record"], root=scratch_root, out=printed.append) == 2
    assert "OPENAI_API_KEY" in printed[-1] and FAKE_KEY not in " ".join(printed)


# ---- 4 and 6: on the REAL recorded cache (skipped until you have run the record step)
REAL = ROOT / CFG["cache"]
recorded = pytest.mark.skipif(not list(REAL.glob("*.json")),
                              reason="run the record step first: ./lantern eval run --provider openai --suite dev --mode record")


@recorded
def test_the_real_cache_holds_no_key_and_no_auth_material():
    needles = ["sk-", "Authorization", "Bearer"]
    if os.environ.get("OPENAI_API_KEY"):
        needles.append(os.environ["OPENAI_API_KEY"])
    assert scan(list(REAL.glob("*.json")), needles) == []


@recorded
def test_one_dev_task_replayed_twice_from_the_real_cache_is_identical():
    def never(body, base_url):
        raise AssertionError("replay must not call the provider")
    a = run_task(ROOT, DEV["dev-01"], TOKEN, provider="openai", chat=never)
    b = run_task(ROOT, DEV["dev-01"], TOKEN, provider="openai", chat=never)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["models"]["loop"] == CFG["routes"]["agent"] and a["models"]["triage"] == CFG["routes"]["triage"]


@recorded
def test_the_dev_suite_replays_from_the_real_cache_with_every_check_reported():
    rows = cli.run_suite(ROOT, "dev", TOKEN, provider="openai")
    assert len(rows) == len(DEV) and all(set(r["checks"]) == {"sequence", "refusal", "isolation"} for r in rows)
    assert all(r["provider"] == "openai" for r in rows)


# ---- regression: record and replay call tools with the SAME argument order (dev-06's cache miss)
def test_record_and_replay_call_tools_with_the_models_own_argument_order(tmp_path):
    """The cache stores answers with sorted keys; a tool error that quotes its input made replay
    diverge. Both modes now rebuild arguments from the model's original string."""
    def reply(b, n):
        if n == 1:   # arguments in NON-alphabetical order, as a real model may send them
            return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {"name": "echo",
                "arguments": '{"zeta": 1, "alpha": 2, "rationale": "r"}'}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1}}
        return completion(b["model"], text="done")
    echo = lambda **kw: {"error": f"input_value={kw!r}"}                       # quotes the order it was given
    fake = FakeChat(reply)
    m = llm.AgentModel(CFG, llm.client(CFG, tmp_path, "record", chat=fake), "gpt-5.4-mini", tools=[])
    _, t1 = loop.run_agent(m, {"echo": echo}, "task", clock.clock)
    second = fake.bodies[1]["messages"][-1]["content"]
    assert "'zeta': 1, 'alpha': 2" in second                                   # the model's order, not sorted
    m2 = llm.AgentModel(CFG, llm.client(CFG, tmp_path, "replay"), "gpt-5.4-mini", tools=[])
    _, t2 = loop.run_agent(m2, {"echo": echo}, "task", clock.clock)            # no CacheMiss
    assert json.dumps(t1, sort_keys=True) == json.dumps(t2, sort_keys=True) and len(fake.bodies) == 2


def test_the_default_provider_is_the_stand_in_so_plain_commands_reproduce_the_graded_outputs():
    assert P["model"]["provider"] == "standin"
    rec = run_task(ROOT, DEV["dev-01"], TOKEN)                         # no --provider: the stand-in
    assert rec["provider"] == "standin" and rec["passed"]
