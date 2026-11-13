"""Part 8 and Part 2: the lantern command.

    ./lantern eval run --suite dev --mode replay                    # grade and print; writes nothing
    ./lantern eval run --suite dev --mode replay --apply            # also writes outputs/tasks/dev.jsonl
    ./lantern eval run --suite dev --mode record --estimate         # estimated cost; NO API call
    ./lantern eval run --suite dev --mode record                    # REAL API calls, once (needs OPENAI_API_KEY)
    ./lantern eval twice --task dev-01                              # one task replayed twice: identical traces?

--provider openai (params.yaml default) uses the real model behind the kit's ReplayClient, with
the cache in evals/replay/; --provider standin uses the kit's offline stand-in. Replay never calls
the provider: a request never recorded is a CacheMiss. A record session stops cleanly before it
passes model.record_budget_usd. Every task runs in its own sandbox. The session token is read
ONLY from CREDITWATCH_TOKEN, the API key ONLY from OPENAI_API_KEY (never printed or stored).
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

from creditwatch.llm import CacheMiss
from services.chat.session import ROOT, SkillsTampered, load_config
from services.evals.runner import run_task
from services.llm.model import RecordBudgetExceeded, Spend
from services.llm.openai_provider import ProviderError


def suite_path(root, suite):
    P, _, _ = load_config(root)
    return Path(root) / (P["data"]["fixture_dir"] + "/tasks/dev.jsonl" if suite == "dev" else "evals/tasks/variant.jsonl")


def load_tasks(root, suite):
    return [json.loads(x) for x in suite_path(root, suite).read_text().splitlines() if x.strip()]


def run_suite(root, suite, token, principal="priya", provider=None, mode="replay", spend=None):
    return [run_task(root, t, token, principal, suite, provider=provider, mode=mode, spend=spend)
            for t in load_tasks(root, suite)]


def table(records):
    out = [f"{'task':8s} {'mandate':10s} {'seq':5s} {'refuse':6s} {'iso':5s} pass  decisions"]
    for r in records:
        dec = ", ".join(f"{a['tool']} {a['decision']}" for a in r["actions"]) or "(no tool call)"
        c = r["checks"]
        out.append(f"{r['task_id']:8s} {r['mandate']:10s} {str(c['sequence']):5s} {str(c['refusal']):6s} "
                   f"{str(c['isolation']):5s} {'PASS' if r['passed'] else 'FAIL'}  {dec}")
    out.append(f"{sum(r['passed'] for r in records)}/{len(records)} tasks pass")
    return out


def _parser():
    ap = argparse.ArgumentParser(prog="lantern")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("eval").add_subparsers(dest="action", required=True)
    run = ev.add_parser("run")
    run.add_argument("--suite", choices=["dev", "variant"], required=True)
    run.add_argument("--mode", choices=["replay", "record"], default="replay")
    run.add_argument("--provider", choices=["openai", "standin"], default=None)
    run.add_argument("--estimate", action="store_true", help="record mode: estimate tokens and cost, call nothing")
    run.add_argument("--apply", action="store_true", help="write outputs/tasks/<suite>.jsonl")
    tw = ev.add_parser("twice")
    tw.add_argument("--task", required=True)
    tw.add_argument("--provider", choices=["openai", "standin"], default=None)
    return ap


def main(argv=None, root=ROOT, out=print):
    logging.getLogger("mcp").setLevel(logging.WARNING)          # the server's INFO lines on refusals
    logging.getLogger("httpx").setLevel(logging.WARNING)        # never log requests
    a = _parser().parse_args(argv)
    P, _, _ = load_config(root)
    principal = os.environ.get("CREDITWATCH_PRINCIPAL", P["agent"]["principal"])
    provider = getattr(a, "provider", None) or P["model"]["provider"]
    token = os.environ.get("CREDITWATCH_TOKEN")
    if a.action == "run" and not suite_path(root, a.suite).exists():
        out(f"variant prompts not released yet: {suite_path(root, a.suite).relative_to(root)} not found")
        return 0
    if not token:
        out("set CREDITWATCH_TOKEN in the environment")
        return 2
    if a.action == "run" and a.mode == "record" and provider == "standin":
        out("--mode record is for the real model; the stand-in needs no recording")
        return 2
    if a.action == "run" and a.mode == "record" and a.estimate:
        from services.llm.estimate import estimate
        rows, total = estimate(root, load_tasks(root, a.suite), token, principal)
        for r in rows:
            out(f"{r['task_id']:8s} {r['model']:14s} {r['steps']} steps  ~{r['tokens_in']:>7,} in  ~{r['tokens_out']:>6,} out  ~${r['usd']:.4f}")
        out(f"ESTIMATE for --suite {a.suite}: ~{total['calls']} calls, ~{total['tokens_in']:,} input and "
            f"~{total['tokens_out']:,} output tokens, ~${total['usd']:.4f} (hard cap ${P['model']['record_budget_usd']:.2f}). "
            "No API call was made.")
        return 0
    if a.action == "run" and a.mode == "record" and not os.environ.get("OPENAI_API_KEY"):
        out("set OPENAI_API_KEY in the environment (it is never printed or stored)")
        return 2
    spend = Spend(P["model"]["record_budget_usd"]) if getattr(a, "mode", "replay") == "record" else None
    try:
        if a.action == "twice":
            task = next(t for t in load_tasks(root, "dev") if t["task_id"] == a.task)
            first = run_task(root, task, token, principal, provider=provider)
            second = run_task(root, task, token, principal, provider=provider)
            out(f"{a.task} replay 1 trace {first['trace_hash']}")
            out(f"{a.task} replay 2 trace {second['trace_hash']}")
            same = json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
            out(f"identical: {same} (provider {provider}, {first['steps']} steps, ${first['usd']:.6f}, passed {first['passed']})")
            return 0 if same else 1
        records = run_suite(root, a.suite, token, principal, provider=provider, mode=a.mode, spend=spend)
    except CacheMiss:
        out("not recorded yet: a request has no entry in the replay cache. Run the record step first "
            "(./lantern eval run --suite dev --mode record), then replay.")
        return 1
    except RecordBudgetExceeded as e:
        out(f"Stopped: {e}")
        return 1
    except ProviderError as e:
        out(f"Provider error: {e}")
        return 1
    except PermissionError as e:
        out(f"Login refused: {e}")
        return 1
    except SkillsTampered as e:
        out(f"Start-up refused: {e}")
        return 1
    for line in table(records):
        out(line)
    if spend is not None:
        out(f"Recorded: {spend.calls} provider calls, ${spend.usd:.4f} spent (cap ${spend.cap:.2f}); "
            f"cache in {P['model']['cache']}/. Everything else now runs in --mode replay.")
    if a.apply:
        path = Path(root) / "outputs" / "tasks" / f"{a.suite}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")
        out(f"Written: outputs/tasks/{a.suite}.jsonl")
    elif spend is None:
        out("Dry run: every task ran in its own sandbox; nothing written. Add --apply to write the results.")
    return 0 if all(r["passed"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
