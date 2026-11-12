"""Part 7: the learning loop. Replays corrections.jsonl through the channel into our
agent, with the client-note task run before and after each correction.

    PYTHONPATH=src python -m services.learning.replay            # DRY RUN: a temporary copy, writes nothing real
    PYTHONPATH=src python -m services.learning.replay --apply    # writes the REAL project (see below)

The session token is read ONLY from the environment (CREDITWATCH_TOKEN). For each
correction: run the task (before); send the correction through our MCP server's
memory_propose_write, where the channel decides its origin; run the task (after);
check adherence, earlier rules and the safety basics.

--apply writes, in the real project:
    agent/PRINCIPAL.md                 one rule line per authenticated correction
    logs/audit/<day>.jsonl             the saved ledger: ids and hashes, never text
    outputs/learning/<message_id>.jsonl   the before and after runs
It refuses to run twice (no duplicated rules).
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import Client

from creditwatch import audit, channel, clock
from services.chat.session import ROOT, SkillsTampered, load_config
from services.learning.agent import run_task
from services.learning.checks import ADHERENCE, safety
from services.mcp.server import build_server, others_of


class AlreadyReplayed(Exception):
    """PRINCIPAL.md already holds rules from these corrections."""


def _jsonl(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def scratch_copy(root, dest):
    """What the agent reads and writes: params.yaml, the fixture data, agent/."""
    P, _, _ = load_config(root)
    shutil.copy(Path(root) / "params.yaml", Path(dest) / "params.yaml")
    shutil.copytree(Path(root) / "data" / "fixture", Path(dest) / "data" / "fixture")
    shutil.copytree(Path(root) / P["agent"]["workspace"], Path(dest) / P["agent"]["workspace"],
                    ignore=shutil.ignore_patterns("__pycache__"))
    return Path(dest)


def replay(root, principal, token, mandate="mandate_a"):
    """Runs the learning loop on `root` and returns one row per correction."""
    root = Path(root)
    P, users, mandates = load_config(root)
    fx = root / P["data"]["fixture_dir"]
    principal_md = root / P["agent"]["workspace"] / "PRINCIPAL.md"
    corrections = _jsonl(fx / "corrections.jsonl")
    done = [c["message_id"] for c in corrections if f"({c['message_id']}, " in principal_md.read_text()]
    if done:
        raise AlreadyReplayed(f"PRINCIPAL.md already holds {done}; restore it from the seed to replay again")

    dev = {t["task_id"]: t for t in _jsonl(fx / "tasks" / "dev.jsonl")}
    day = clock.clock().date().isoformat()
    (root / "logs" / "audit").mkdir(parents=True, exist_ok=True)
    led = audit.Ledger(path=root / "logs" / "audit" / f"{day}.jsonl", clock=clock.clock)   # saved to disk
    out_dir = root / "outputs" / "learning"
    out_dir.mkdir(parents=True, exist_ok=True)
    others = others_of(root, P, mandates, mandate)
    rows = []
    for c in corrections:
        cid = c["message_id"]
        task = next((t for t in dev.values() if t.get("after_correction") == cid), dev["dev-01"])
        before = run_task(root, principal, token, mandate, task["prompt"], led, run_id=f"{cid}-before")

        s = channel.login(principal, token, mandate, users)          # the correction, through the channel

        async def propose():
            async with Client(build_server(s, led, root=root)) as cl:
                return await cl.call_tool("memory_propose_write", {"scope": mandate, "content": c["content"],
                                                                   "message_id": cid})
        r = asyncio.run(propose())
        written = r.structured_content or {"result": "error", "origin": None}

        after = run_task(root, principal, token, mandate, task["prompt"], led, run_id=f"{cid}-after")
        records = []
        for phase, run in (("before", before), ("after", after)):
            records.append({"correction": cid, "phase": phase, "task_id": task["task_id"], "mandate": mandate,
                            "run_id": f"{cid}-{phase}",
                            "adherence": {m: check(run["final_response"]) for m, check in ADHERENCE.items()},
                            "safety": safety(run["final_response"], run["actions"], others),
                            "final_response": run["final_response"], "actions": run["actions"]})
        (out_dir / f"{cid}.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in records),
                                             encoding="utf-8")
        earlier = [row["correction"] for row in rows]
        a = records[1]
        rows.append({"correction": cid, "task_id": task["task_id"], "result": written["result"],
                     "origin": written["origin"], "before": records[0]["adherence"][cid],
                     "after": a["adherence"][cid],
                     "earlier_rules_held": all(a["adherence"][e] for e in earlier),
                     "regressions": [k for k, ok in a["safety"].items() if not ok]
                                    + [e for e in earlier if not a["adherence"][e]]})
    return rows


def table(rows):
    out = [f"{'correction':10s} {'task':6s} {'rule':9s} {'before':7s} {'after':6s} {'earlier held':13s} regressions"]
    for r in rows:
        out.append(f"{r['correction']:10s} {r['task_id']:6s} {r['result']:9s} {str(r['before']):7s} "
                   f"{str(r['after']):6s} {str(r['earlier_rules_held']):13s} {', '.join(r['regressions']) or 'none'}")
    return out


def main(argv=None, root=ROOT, out=print):
    ap = argparse.ArgumentParser(description="Replay corrections.jsonl through the agent, before and after")
    ap.add_argument("--mandate", default="mandate_a")
    ap.add_argument("--apply", action="store_true", help="write the REAL project (default: dry run on a copy)")
    a = ap.parse_args(argv)
    P, _, _ = load_config(root)
    principal = os.environ.get("CREDITWATCH_PRINCIPAL", P["agent"]["principal"])
    token = os.environ.get("CREDITWATCH_TOKEN")
    if not token:
        out("set CREDITWATCH_TOKEN in the environment")
        return 2
    try:
        if a.apply:
            rows = replay(root, principal, token, a.mandate)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                rows = replay(scratch_copy(root, tmp), principal, token, a.mandate)
    except AlreadyReplayed as e:
        out(f"Refused: {e}")
        return 1
    except PermissionError as e:
        out(f"Login refused: {e}")
        return 1
    except SkillsTampered as e:
        out(f"Start-up refused: {e}")
        return 1
    for line in table(rows):
        out(line)
    if a.apply:
        out("Written: agent/PRINCIPAL.md, logs/audit/, outputs/learning/")
    else:
        out("Dry run on a temporary copy: nothing written in the real project. Add --apply to write it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
