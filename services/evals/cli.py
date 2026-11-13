"""Part 8: the lantern command.

    ./lantern eval run --suite dev --mode replay            # DRY RUN: grade and print, write nothing
    ./lantern eval run --suite dev --mode replay --apply    # also writes outputs/tasks/dev.jsonl

--suite dev reads data/fixture/tasks/dev.jsonl; --suite variant reads evals/tasks/variant.jsonl
(not released yet). --mode replay runs offline and deterministic, with no provider calls; the
stand-in model needs no cache. --mode record arrives with the real model (Part 2).
Every task runs in its own sandbox copy: the only real file --apply writes is the results file.
The session token is read ONLY from the environment (CREDITWATCH_TOKEN).
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

from services.chat.session import ROOT, SkillsTampered, load_config
from services.evals.runner import run_task


def suite_path(root, suite):
    P, _, _ = load_config(root)
    return Path(root) / (P["data"]["fixture_dir"] + "/tasks/dev.jsonl" if suite == "dev" else "evals/tasks/variant.jsonl")


def run_suite(root, suite, token, principal="priya"):
    tasks = [json.loads(x) for x in suite_path(root, suite).read_text().splitlines() if x.strip()]
    return [run_task(root, t, token, principal, suite) for t in tasks]


def table(records):
    out = [f"{'task':8s} {'mandate':10s} {'seq':5s} {'refuse':6s} {'iso':5s} pass  decisions"]
    for r in records:
        dec = ", ".join(f"{a['tool']} {a['decision']}" for a in r["actions"]) or "(no tool call)"
        c = r["checks"]
        out.append(f"{r['task_id']:8s} {r['mandate']:10s} {str(c['sequence']):5s} {str(c['refusal']):6s} "
                   f"{str(c['isolation']):5s} {'PASS' if r['passed'] else 'FAIL'}  {dec}")
    out.append(f"{sum(r['passed'] for r in records)}/{len(records)} tasks pass")
    return out


def main(argv=None, root=ROOT, out=print):
    logging.getLogger("mcp").setLevel(logging.WARNING)          # the server's INFO lines on refusals
    ap = argparse.ArgumentParser(prog="lantern")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("eval").add_subparsers(dest="action", required=True).add_parser("run")
    ev.add_argument("--suite", choices=["dev", "variant"], required=True)
    ev.add_argument("--mode", choices=["replay", "record"], default="replay")
    ev.add_argument("--apply", action="store_true", help="write outputs/tasks/<suite>.jsonl")
    a = ap.parse_args(argv)
    if a.mode == "record":
        out("--mode record needs the real model behind the replay client (Part 2); use --mode replay")
        return 2
    if not suite_path(root, a.suite).exists():
        out(f"variant prompts not released yet: {suite_path(root, a.suite).relative_to(root)} not found")
        return 0
    P, _, _ = load_config(root)
    token = os.environ.get("CREDITWATCH_TOKEN")
    if not token:
        out("set CREDITWATCH_TOKEN in the environment")
        return 2
    try:
        records = run_suite(root, a.suite, token, os.environ.get("CREDITWATCH_PRINCIPAL", P["agent"]["principal"]))
    except PermissionError as e:
        out(f"Login refused: {e}")
        return 1
    except SkillsTampered as e:
        out(f"Start-up refused: {e}")
        return 1
    for line in table(records):
        out(line)
    if a.apply:
        path = Path(root) / "outputs" / "tasks" / f"{a.suite}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")
        out(f"Written: outputs/tasks/{a.suite}.jsonl")
    else:
        out("Dry run: every task ran in its own sandbox; nothing written. Add --apply to write the results.")
    return 0 if all(r["passed"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
