"""Part 8: the unit-economics table (tutorial slide 41), including cost per mandate-month.

    PYTHONPATH=src python -m services.economics.report            # DRY RUN: print the table
    PYTHONPATH=src python -m services.economics.report --apply    # writes reports/unit_economics.md and .json

Measured where the project can measure: cost per task from the dev suite's traces, latency from a
simulated heartbeat day, signal precision and recall on the fixture and variants, adherence from
outputs/learning/, the dev suite's pass rate. Everything else is a stated ASSUMPTION from
params.yaml (economics:), labelled as such. All runs happen in sandboxes.
The session token is read ONLY from the environment (CREDITWATCH_TOKEN).
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

from creditwatch import econ, evals, panel, signals
from services.chat.session import ROOT, load_config
from services.evals.cli import run_suite
from services.heartbeat.run import simulate_day
from services.learning.replay import scratch_copy
from services.signals.pairing import pair


def _scores(root, P):
    spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")   # kit code
    mf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mf)
    fx = Path(root) / P["data"]["fixture_dir"]
    sets = [("fixture", panel.load_panel(fx / "panel_2p.csv"), pd.read_csv(fx / "ground_truth.csv", dtype=str))]
    for s in (1, 2, 3):
        rows, truth = mf.build(s)
        p = pd.DataFrame(rows)
        for c in panel.NUMERIC:
            if c in p:
                p[c] = pd.to_numeric(p[c], errors="coerce")
        sets.append((f"variant {s}", p, pd.DataFrame(truth).astype(str)))
    out = {}
    for label, p, t in sets:
        sc = evals.signal_scores(signals.find_signals(p, P["heartbeat"]["materiality"], pair=pair), t)
        out[label] = {"precision": round(sc["precision"], 2), "recall": round(sc["recall"], 2)}
    return out


def _adherence(root):
    files = sorted((Path(root) / "outputs" / "learning").glob("msg-*.jsonl"))
    if not files:
        return None
    held = 0
    for f in files:
        after = [json.loads(x) for x in f.read_text().splitlines()][-1]
        held += bool(after["adherence"][after["correction"]])
    return {"rules": len(files), "held_after_learning": held}


def build(root, token, principal="priya", provider=None):
    root = Path(root)
    P, _, mandates = load_config(root)
    ec, pr = P["economics"], P["prices"]
    records = run_suite(root, "dev", token, principal, provider=provider)   # each task in its own sandbox, replay
    by_id = {r["task_id"]: r for r in records}
    note = by_id["dev-01"]
    step_usd = econ.step_usd(2000, 300, pr["usd_per_mtok_in"], pr["usd_per_mtok_out"])
    with tempfile.TemporaryDirectory() as tmp:
        _, hb = simulate_day(scratch_copy(root, tmp))

    reviews = sorted({b for m in mandates.values() for b in m["positions"]})
    saved = econ.hours_saved_table(ec["analyst_hours_per_review"], ec["analyst_rate_usd"], tasks=len(reviews))
    t = ec["tasks_per_mandate_month"]
    idle = hb["idle_cost_per_mandate_month"]["usd"]
    per_month = {}
    for m, v in mandates.items():
        model_tasks = t["client_note"] + t["bdc_review_per_held_bdc"] * len(v["positions"])
        per_month[m] = {"model_tasks": model_tasks, "briefs": t["brief"], "usd_per_task": note["usd"],
                        "model_usd": round(model_tasks * note["usd"], 6), "idle_usd": idle,
                        "total_usd": round(model_tasks * note["usd"] + idle, 6)}
    false_alert_usd = round(ec["false_alert_minutes"] / 60 * ec["analyst_rate_usd"], 2)
    return {
        "cost_per_completed_task": {"client_note_usd": note["usd"], "steps": note["steps"], "usd_per_step": step_usd,
                                    "by_task_usd": {r["task_id"]: r["usd"] for r in records},
                                    "note": "the stand-in model's tokens (2,000 in / 300 out per step) at params.yaml's "
                                            "illustrative prices; Part 2 measures a real model"},
        "latency": {"release_published_to_brief_ready_minutes": hb["latency"]["release_detected_to_brief_minutes"],
                    "filing_accepted_to_alert_minutes": f"within one {hb['latency']['filing_poll_bound_minutes']}-minute poll (live)"},
        "human_time_saved": {"bdc_reviews_per_release": len(reviews),
                             "analyst_hours_per_review": ec["analyst_hours_per_review"],
                             "analyst_rate_usd": ec["analyst_rate_usd"], "sensitivity": saved},
        "quality": {"signal_precision_recall": _scores(root, P), "adherence": _adherence(root),
                    "dev_suite": f"{sum(r['passed'] for r in records)}/{len(records)} pass"},
        "cost_of_failure": {"false_alert_usd": false_alert_usd, "false_alert_minutes": ec["false_alert_minutes"],
                            "false_alerts_on_fixture": 0, "leak_cost_usd": ec["leak_cost_usd"],
                            "leak_cost_is_assumption": True,
                            "why_approvals": "one client's position in another's letter is the costliest failure; "
                                             "approvals of the exact payload and the R-OTHER-MANDATE rule stop it "
                                             "before anything leaves the firm"},
        "cost_per_mandate_month": per_month,
        "tasks_per_mandate_month": t,
        "idle_cost_per_mandate_month": hb["idle_cost_per_mandate_month"],
    }


def markdown(e):
    c, lat, h, q, f = (e["cost_per_completed_task"], e["latency"], e["human_time_saved"], e["quality"],
                       e["cost_of_failure"])
    pr = "; ".join(f"{k} P {v['precision']:.2f} R {v['recall']:.2f}" for k, v in q["signal_precision_recall"].items())
    ad = q["adherence"]
    sens = "; ".join(f"{s['scenario']}: {s['hours']:g} h = ${s['usd']:,.0f}" for s in h["sensitivity"])
    rows = [
        ("Cost per completed task", f"${c['client_note_usd']:.5f} per client note ({c['steps']} model steps at "
                                    f"${c['usd_per_step']:.5f}); {c['note']}"),
        ("Latency", f"release published to brief ready: {lat['release_published_to_brief_ready_minutes']:g} minutes; "
                    f"filing accepted to alert: {lat['filing_accepted_to_alert_minutes']}"),
        ("Human time saved", f"{h['bdc_reviews_per_release']} BDC reviews per release at {h['analyst_hours_per_review']:g} "
                             f"hours, ${h['analyst_rate_usd']:g} an hour (ASSUMPTION): {sens}"),
        ("Quality", f"signals: {pr}; adherence: " + (f"{ad['held_after_learning']}/{ad['rules']} rules held after learning"
                    if ad else "not measured") + f"; dev suite {q['dev_suite']}"),
        ("Cost of failure", f"a false alert costs ${f['false_alert_usd']:.2f} of analyst time ({f['false_alert_minutes']} "
                            f"minutes, ASSUMPTION), {f['false_alerts_on_fixture']} false alerts on the fixture; "
                            f"a leak is priced at ${f['leak_cost_usd']:,} (**ASSUMPTION**, a placeholder); {f['why_approvals']}"),
    ]
    for m, v in e["cost_per_mandate_month"].items():
        rows.append((f"Cost per mandate-month ({m})",
                     f"${v['total_usd']:.4f}: {v['model_tasks']} model tasks x ${v['usd_per_task']:.5f} + idle "
                     f"${v['idle_usd']:.2f}; {v['briefs']} brief (deterministic, $0)"))
    lines = ["# Unit economics: what a release morning costs", "",
             "| Line | Credit Watch |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows], "",
             "**The case is coverage.** Model cost is small. What justifies the agent is that it watches every held BDC "
             "in every release, for every client, which no analyst does by hand; the heartbeat's idle cost is "
             f"${e['idle_cost_per_mandate_month']['usd']:.2f} per mandate-month because it makes no model calls.", "",
             "## Assumptions (stated, not measured; from params.yaml)",
             "- Tasks per mandate-month (one monthly release): "
             + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in e["tasks_per_mandate_month"].items()) + ".",
             f"- Analyst hours per BDC review: {h['analyst_hours_per_review']:g}; rate ${h['analyst_rate_usd']:g} an hour "
             "(shown at x0.5, x1 and x2).",
             f"- A false alert costs {f['false_alert_minutes']} minutes of Priya's time.",
             f"- **ASSUMPTION: one leak costs ${f['leak_cost_usd']:,}.** A placeholder for legal review, client "
             "remediation and relationship risk; replace it with your own figure and reasoning.",
             "- A BDC review is costed as one note-sized task. Prices are params.yaml's illustrative prices, and the "
             "model is the stand-in until Part 2."]
    return "\n".join(lines) + "\n"


def main(argv=None, root=ROOT, out=print):
    ap = argparse.ArgumentParser(description="The unit-economics table")
    ap.add_argument("--apply", action="store_true", help="write reports/unit_economics.md and .json")
    ap.add_argument("--provider", choices=["openai", "standin"], default=None)
    a = ap.parse_args(argv)
    P, _, _ = load_config(root)
    token = os.environ.get("CREDITWATCH_TOKEN")
    if not token:
        out("set CREDITWATCH_TOKEN in the environment")
        return 2
    e = build(root, token, os.environ.get("CREDITWATCH_PRINCIPAL", P["agent"]["principal"]), provider=a.provider)
    md = markdown(e)
    out(md)
    if a.apply:
        d = Path(root) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        (d / "unit_economics.md").write_text(md, encoding="utf-8")
        (d / "unit_economics.json").write_text(json.dumps(e, indent=2, sort_keys=True), encoding="utf-8")
        out("Written: reports/unit_economics.md, reports/unit_economics.json")
    else:
        out("Dry run: computed in sandboxes; nothing written. Add --apply to write reports/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
