"""Part 5: what the heartbeat costs and how fast it is, read from what it delivered.

    latency            release detected -> brief ready; filing accepted -> alert or hold
    alerts per mandate-week   deliveries per mandate in the week ending on the day measured
    idle cost per mandate-month   scheduled runs in a month x model spend per run, per mandate

The heartbeat jobs make no model calls (signals, routing and briefs are deterministic), so on the
fixture the idle model spend is $0.00; the run counts are reported so the number can be checked.
Compute and hosting are not modelled.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from services.chat.session import load_config

RUNS_PER_MONTH = {                       # from the DAG schedules (HEARTBEAT.md), a 30-day month
    "release_watch_dag": 30,             # daily 06:00
    "edgar_watch_dag": 48 * 30,          # every 30 minutes
    "monthly_brief_dag": 22,             # weekdays 07:30 (a brief is written on release days only)
    "hygiene_dag": 4,                    # Mondays 08:00
}
MODEL_USD_PER_RUN = 0.0                  # the jobs call no model
EDGAR_POLL_MINUTES = 30


def _jsonl(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines()] if Path(path).exists() else []


def _minutes(a, b):
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60, 1)


def compute(root, day):
    root = Path(root)
    _, _, mandates = load_config(root)
    hb = root / "outputs" / "heartbeat"
    alerts = _jsonl(hb / "alerts.jsonl")
    events = _jsonl(root / "logs" / "audit" / f"{day}.jsonl")

    release_ts = min((a["ts"] for a in alerts if a["trigger"].startswith("release")), default=None)
    briefs = [e["ts"] for e in events if e["kind"] == "brief_sent"]
    filing = [_minutes(a["accepted"].replace("Z", "+00:00"), a["ts"]) for a in alerts if a.get("accepted")]

    end = datetime.fromisoformat(day).date()
    week = [a for a in alerts if end - timedelta(days=6) <= datetime.fromisoformat(a["ts"]).date() <= end]
    per_week = {m: {"alert": sum(a["mandate"] == m and a["action"] == "alert" for a in week),
                    "hold": sum(a["mandate"] == m and a["action"] == "hold" for a in week)} for m in mandates}
    for v in per_week.values():
        v["total"] = v["alert"] + v["hold"]

    runs = sum(RUNS_PER_MONTH.values())
    return {
        "day": day,
        "latency": {
            "release_detected_to_brief_minutes": _minutes(release_ts, min(briefs)) if release_ts and briefs else None,
            "filing_accepted_to_delivery_minutes": {"max": max(filing), "min": min(filing)} if filing else None,
            "filing_note": "the fixture's EDGAR feed was recorded in August, so this measures the replay; "
                           f"live, a filing is seen within one poll ({EDGAR_POLL_MINUTES} minutes)",
            "filing_poll_bound_minutes": EDGAR_POLL_MINUTES,
        },
        "alerts_per_mandate_week": {"week_ending": day, **per_week},
        "idle_cost_per_mandate_month": {
            "runs_per_month": RUNS_PER_MONTH, "model_usd_per_run": MODEL_USD_PER_RUN,
            "usd": round(runs * MODEL_USD_PER_RUN / len(mandates), 4),
            "note": "no model calls in the heartbeat; compute and hosting are not modelled",
        },
    }


def write(root, day):
    m = compute(root, day)
    path = Path(root) / "outputs" / "heartbeat" / "metrics.json"
    path.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")
    return m
