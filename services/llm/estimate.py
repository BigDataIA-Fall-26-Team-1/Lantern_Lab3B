"""Part 2: estimate a record session's tokens and cost WITHOUT calling the provider.

Each task is run with the kit's stand-in (same harness, same tools, same observations) to learn
how many model steps it takes and what text the model would be sent at each step: the system
prompt, the assembled context (plus the one skill triage would open), the 12 tool schemas, and
the tool results so far. Input tokens are counted the way context assembly counts them (about
4 characters a token); output tokens per step are an ASSUMPTION (reasoning included).
"""
import asyncio
import json
import tempfile

from mcp import Client

from creditwatch import audit, channel, clock
from services.chat.session import load_config
from services.learning.replay import scratch_copy
from services.llm.model import SYSTEM, function_tools, price
from services.llm.routes import TRIAGE, route_for
from services.mcp.server import build_server

OUT_TOKENS_PER_STEP = 600            # ASSUMPTION: answer plus hidden reasoning, per agent step
TRIAGE_OUT_TOKENS = 150              # ASSUMPTION, per triage call
SKILL_TOKENS = 200                   # one opened SKILL.md


def ntok(s):
    return max(1, len(s) // 4)


def _schema_tokens(root, token):
    P, users, _ = load_config(root)
    s = channel.login("priya", token, "mandate_a", users)

    async def go():
        async with Client(build_server(s, audit.Ledger(clock=clock.clock), root=root)) as c:
            return (await c.list_tools()).tools
    return ntok(json.dumps(function_tools(asyncio.run(go()))))


def estimate(root, tasks, token, principal="priya"):
    from services.evals.runner import run_in          # here, to keep the import graph one-way
    P, _, _ = load_config(root)
    cfg = P["model"]
    rows, total = [], {"usd": 0.0, "tokens_in": 0, "tokens_out": 0, "calls": 0}
    with tempfile.TemporaryDirectory() as tmp:
        sb = scratch_copy(root, tmp)
        schemas = _schema_tokens(sb, token)
        for t in tasks:
            rec, ex = run_in(sb, t, token, principal, provider="standin")
            model = cfg["routes"][route_for(t.get("category"))]
            base = ntok(SYSTEM) + ntok(ex["text"]) + SKILL_TOKENS + schemas
            obs = [ntok(x) for x in ex["observations"]]
            steps = rec["steps"]
            t_in = sum(base + sum(obs[:k]) + k * 60 for k in range(steps))     # +60: each earlier tool call
            t_out = steps * OUT_TOKENS_PER_STEP
            tri_in = ntok(TRIAGE) + 300 + ntok(t["prompt"])
            usd = price(cfg, model, t_in, t_out) + price(cfg, cfg["routes"]["triage"], tri_in, TRIAGE_OUT_TOKENS)
            rows.append({"task_id": t["task_id"], "model": model, "steps": steps, "tokens_in": t_in + tri_in,
                         "tokens_out": t_out + TRIAGE_OUT_TOKENS, "usd": round(usd, 4)})
            total["usd"] += usd
            total["tokens_in"] += t_in + tri_in
            total["tokens_out"] += t_out + TRIAGE_OUT_TOKENS
            total["calls"] += steps + 1
    total["usd"] = round(total["usd"], 4)
    return rows, total
