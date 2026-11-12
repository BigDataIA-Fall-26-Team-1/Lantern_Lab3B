"""Part 5: a simulated heartbeat day. Runs the four jobs the DAGs call, at their scheduled times,
on the fixture's recorded feeds with an injected clock: what Airflow would run, without Airflow.

    PYTHONPATH=src python -m services.heartbeat.run                   # DRY RUN: a temporary copy
    PYTHONPATH=src python -m services.heartbeat.run --apply           # writes the REAL project

Schedule (HEARTBEAT.md, New York time): release_watch 06:00 daily; edgar_watch every 30 minutes;
monthly_brief 07:30 on weekdays; hygiene 08:00 on Mondays (--force-hygiene runs it on any day).
The day starts at the fixture morning (LANTERN_CLOCK, 06:00) and runs to 23:30.

--apply writes, in the real project:
    outputs/heartbeat/alerts.jsonl, decisions.jsonl, briefs/<day>-<mandate>.md, metrics.json
    (and hygiene/<day>.md when hygiene runs); logs/audit/<day>.jsonl (appended);
    .cache/heartbeat/state.json (git-ignored). It refuses to run the same day twice.
"""
import argparse
import sys
import tempfile

from creditwatch import clock
from services.chat.session import ROOT
from services.heartbeat import brief_job, edgar_job, hygiene_job, release_job
from services.heartbeat import metrics
from services.heartbeat.deliver import Out
from services.learning.replay import scratch_copy


class AlreadyRun(Exception):
    """This day was already simulated in this project."""


def schedule(start, force_hygiene=False):
    """(time, name, job) for one day, in time order; release before edgar at the same minute."""
    day = []
    for h in range(start.hour, 24):
        for mi in (0, 30):
            t = start.replace(hour=h, minute=mi)
            if t < start:
                continue
            if (h, mi) == (6, 0):
                day.append((t, "release_watch", release_job))
            if (h, mi) == (7, 30) and t.weekday() < 5:
                day.append((t, "monthly_brief", brief_job))
            if (h, mi) == (8, 0) and (t.weekday() == 0 or force_hygiene):
                day.append((t, "hygiene", hygiene_job))
            day.append((t, "edgar_watch", edgar_job))
    order = {"release_watch": 0, "edgar_watch": 1, "monthly_brief": 2, "hygiene": 3}
    return sorted(day, key=lambda x: (x[0], order[x[1]]))


def simulate_day(root, start=None, force_hygiene=False):
    start = start or clock.clock()
    day = start.date().isoformat()
    out = Out(root, start)
    st = out.state()
    if day in st.get("days_run", []):
        raise AlreadyRun(f"{day} was already simulated here (see .cache/heartbeat/state.json)")
    runs = []
    for t, name, job in schedule(start, force_hygiene):
        r = job(root, t)
        if name != "edgar_watch" or r.get("delivered"):
            runs.append((t.strftime("%H:%M"), name, r))
    st = out.state()
    out.save({**st, "days_run": sorted(set(st.get("days_run", [])) | {day})})
    return runs, metrics.write(root, day)


def _summary(runs, m):
    lines = []
    for t, name, r in runs:
        if name == "release_watch":
            got = {k: f"{len(v)} {v[0]}" for k, v in r["delivered"].items()} if r["delivered"] else {}
            lines.append(f"{t} release_watch  release {r['release']} published={r['published']} delivered {got}")
        elif name == "edgar_watch":
            lines.append(f"{t} edgar_watch    delivered { {k: f'{len(v)} {v[0]}' for k, v in r['delivered'].items()} }")
        elif name == "monthly_brief":
            lines.append(f"{t} monthly_brief  briefs written {r['briefs']}")
        else:
            lines.append(f"{t} hygiene        {r}")
    lat = m["latency"]
    lines += ["", f"latency: release detected -> brief ready {lat['release_detected_to_brief_minutes']} minutes; "
              f"filings: within one {lat['filing_poll_bound_minutes']}-minute poll when live (recorded feed: "
              f"{lat['filing_accepted_to_delivery_minutes']} minutes)",
              f"alerts per mandate-week: { {k: v for k, v in m['alerts_per_mandate_week'].items() if k != 'week_ending'} }",
              f"idle cost per mandate-month: ${m['idle_cost_per_mandate_month']['usd']:.2f} "
              f"({sum(m['idle_cost_per_mandate_month']['runs_per_month'].values())} scheduled runs, no model calls)"]
    return lines


def main(argv=None, root=ROOT, out=print):
    ap = argparse.ArgumentParser(description="Simulate one heartbeat day on the fixture")
    ap.add_argument("--apply", action="store_true", help="write the REAL project (default: dry run on a copy)")
    ap.add_argument("--force-hygiene", action="store_true", help="run hygiene even if the day is not a Monday")
    a = ap.parse_args(argv)
    try:
        if a.apply:
            runs, m = simulate_day(root, force_hygiene=a.force_hygiene)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                runs, m = simulate_day(scratch_copy(root, tmp), force_hygiene=a.force_hygiene)
    except AlreadyRun as e:
        out(f"Refused: {e}")
        return 1
    for line in _summary(runs, m):
        out(line)
    out("Written: outputs/heartbeat/, logs/audit/, .cache/heartbeat/" if a.apply else
        "Dry run on a temporary copy: nothing written in the real project. Add --apply to write it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
