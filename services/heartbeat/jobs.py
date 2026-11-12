"""Part 5: the four heartbeat jobs. The DAGs in dags/ are thin: each task calls one of these.

All four run on the fixture's recorded feeds with an injected clock, so tests and the simulated
day run exactly what Airflow would run. Every finding goes only to mandates that hold the BDC
(heartbeat.route), is held for the brief in quiet hours (heartbeat.decide), and is never
delivered twice (keys kept in .cache/heartbeat/state.json).
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

from creditwatch import heartbeat, panel, signals
from services.chat.session import ROOT, load_config
from services.heartbeat.deliver import Out, now_or_clock
from services.mcp.server import WATCH_LINE
from services.signals.pairing import pair


def short(name):
    return " ".join(w if w == "BDC" else w.title() for w in name.replace("FIXTURE ", "").split())


def _holding(identifier):
    parts = identifier.split(" | ")
    return parts[1] if len(parts) >= 3 else identifier


def signal_text(s):
    """One plain sentence, prices in points (the same wording as the signal-explainer skill)."""
    b, a = s["before"], s["after"]
    if s["rule"] == "MARKDOWN":
        what = f"marked down {b - a:.1f} points ({b:.1f} to {a:.1f})"
    elif s["rule"] == "PIK_UP":
        what = f"PIK up {a - b:.2f} percentage points ({b:.2f}% to {a:.2f}%)"
    else:
        months = round((date.fromisoformat(a) - date.fromisoformat(b)).days / 30.44)
        what = f"maturity moved out {months} months ({b} to {a})"
    return f"{short(s['name'])}: {_holding(s['identifier'])} {what}"


def _ctx(root, now):
    root = Path(root)
    P, _, mandates = load_config(root)
    if not P["data"]["use_fixture"]:
        raise NotImplementedError("live mode: wire the SEC release and EDGAR feeds; the lab runs on the fixture")
    held = {m: list(v["positions"]) for m, v in mandates.items()}
    return root, P, mandates, held, Out(root, now)


# ---------------------------------------------------------------- release_watch_dag: daily 06:00
def release_job(root=ROOT, now=None):
    now = now_or_clock(now)
    root, P, mandates, held, out = _ctx(root, now)
    fx = root / P["data"]["fixture_dir"]
    heads = json.loads((fx / "release_heads.json").read_text())
    st = out.state()
    tick = heartbeat.release_tick(st["last_release"] or heads["last_seen"],
                                  lambda u: heads["heads"].get(u, 404), P["sec"]["base_url"])
    if not tick["published"]:
        out.decision({"ts": now.isoformat(), "job": "release_watch", "release": tick["release"],
                      "action": "not_published"})
        return {"release": tick["release"], "published": False, "delivered": {}}
    pan = panel.load_panel(root / P["data"]["panel"])
    found = signals.find_signals(pan, P["heartbeat"]["materiality"], pair=pair)
    seen = set(st["seen"])
    delivered = {}
    for m, sig in heartbeat.route(found, held).items():
        by_key = {heartbeat.dedup_key(m, f"{s.adsh}#{s.row}", s.rule): s for s in sig.itertuples()}
        for d in heartbeat.decide(sig, m, seen, now):
            s = by_key[d["key"]]._asdict()
            out.alert({"alert_id": f"al-{d['key']}", "ts": now.isoformat(), "mandate": m, "bdc": s["name"],
                       "trigger": f"release {tick['release']}", "rule": s["rule"], "text": signal_text(s),
                       "citations": [s["adsh"], f"row {s['row']}", f"prior row {s['prior_row']}"],
                       "action": d["action"], "release": tick["release"]})
            delivered.setdefault(m, []).append(d["action"])
    out.save({**st, "last_release": tick["release"], "release_day": now.date().isoformat(), "seen": sorted(seen)})
    return {"release": tick["release"], "published": True, "delivered": delivered}


# ---------------------------------------------------------------- edgar_watch_dag: every 30 minutes
def edgar_job(root=ROOT, now=None):
    now = now_or_clock(now)
    root, P, mandates, held, out = _ctx(root, now)
    fx = root / P["data"]["fixture_dir"]
    recent = json.loads((fx / "edgar_recent.json").read_text())
    registry = json.loads((root / P["data"]["registry"]).read_text())
    empty = {"form": [], "items": [], "accessionNumber": [], "acceptanceDateTime": []}
    st = out.state()
    seen = set(st["seen"])
    delivered = {}
    for bdc, cik in registry["bdc_cik"].items():
        new = heartbeat.new_filings(recent.get(cik, empty), seen=set())
        for m in mandates:
            for d in heartbeat.filing_decisions(new, bdc, held[m], m, seen, now):
                if d["action"] == "not_material":            # a non-holder: logged, never delivered
                    out.decision({"ts": now.isoformat(), "job": "edgar_watch", "mandate": m, "bdc": bdc,
                                  "trigger": d["accn"], "rule": "FILING", "action": "not_material"})
                    continue
                out.alert({"alert_id": f"al-{d['key']}", "ts": now.isoformat(), "mandate": m, "bdc": bdc,
                           "trigger": d["accn"], "rule": "FILING",
                           "text": f"{short(bdc)} filed a {d['form']} (accepted {d['accepted']})",
                           "citations": [d["accn"]], "action": d["action"], "accepted": d["accepted"]})
                delivered.setdefault(m, []).append(d["action"])
    out.save({**st, "seen": sorted(seen)})
    return {"delivered": delivered}


# ---------------------------------------------------------------- monthly_brief_dag: 07:30 on release weekdays
def brief_job(root=ROOT, now=None):
    """One brief per mandate, for Priya, holding that mandate's held items only."""
    now = now_or_clock(now)
    root, P, mandates, held, out = _ctx(root, now)
    st = out.state()
    day = now.date().isoformat()
    if now.weekday() >= 5 or st["release_day"] != day:
        out.decision({"ts": now.isoformat(), "job": "monthly_brief", "action": "no_brief",
                      "reason": "weekend" if now.weekday() >= 5 else "not a release day"})
        return {"briefs": {}}
    alerts_file = out.dir / "alerts.jsonl"
    alerts = [json.loads(x) for x in alerts_file.read_text().splitlines()] if alerts_file.exists() else []
    briefed = set(st["briefed"])
    pan = panel.load_panel(root / P["data"]["panel"])
    report = signals.pairing_report(pan, pair(pan)).set_index("name")
    written = {}
    for m in mandates:
        items = [a for a in alerts if a["mandate"] == m and a["action"] == "hold" and a["alert_id"] not in briefed]
        lines = [f"# Release brief for Priya: {m} ({mandates[m]['client']}), {day}", ""]
        for bdc in held[m]:
            sig = [a for a in items if a["bdc"] == bdc and a["rule"] != "FILING"]
            fil = [a for a in items if a["bdc"] == bdc and a["rule"] == "FILING"]
            if bdc in report.index:
                r = report.loc[bdc]
                cov = f"{int(r['paired'])} of {int(r['debt_holdings'])} debt holdings paired, {int(r['unpaired'])} unpaired"
            else:
                cov = "not in the panel"
            lines += [f"## {short(bdc)}", f"Coverage: {cov}."]
            lines += [f"- {a['text']} [{', '.join(a['citations'])}]" for a in sig] or ["- No material signal."]
            lines += [f"- Filing: {a['text']} [{a['citations'][0]}]" for a in fil]
            lines.append("")
        text = "\n".join(lines)
        out.report("briefs", f"{day}-{m}.md", text, mandate=m, items=len(items))
        briefed |= {a["alert_id"] for a in items}
        written[m] = len(items)
    out.save({**st, "briefed": sorted(briefed)})
    return {"briefs": written}


# ---------------------------------------------------------------- hygiene_dag: Mondays 08:00
def _business_day(year, month, n):
    d, k = date(year, month, 1), 0
    while True:
        if d.weekday() < 5:
            k += 1
            if k == n:
                return d
        d += timedelta(days=1)


def _next_business_days(start, n):
    out, d = [], start
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def hygiene_job(root=ROOT, now=None):
    """Watchlist entries not reviewed in stale_days; client reporting dates in the next
    reporting_window_business_days (weekends skipped; public holidays are not modelled)."""
    now = now_or_clock(now)
    root, P, mandates, held, out = _ctx(root, now)
    h = P["heartbeat"]["hygiene"]
    ws = root / P["agent"]["workspace"]
    today = now.date()
    window = _next_business_days(today, h["reporting_window_business_days"])
    lines, found = [f"# Hygiene for Priya, {today.isoformat()}", ""], {}
    for m in mandates:
        wl = (ws / "mandates" / m / "WATCHLIST.md").read_text(encoding="utf-8")
        stale = [g for g in map(WATCH_LINE.match, wl.splitlines())
                 if g and (today - date.fromisoformat(g["added"])).days > h["stale_days"]]
        mandate_md = (ws / "mandates" / m / "MANDATE.md").read_text(encoding="utf-8")
        rule = re.search(r"by the (\d+)(?:st|nd|rd|th) business day", mandate_md)
        if rule:
            n = int(rule.group(1))
            due = [d for d in (_business_day(today.year, today.month, n),
                               _business_day(today.year + (today.month == 12), today.month % 12 + 1, n)) if d > today][0]
            report = f"next {due.isoformat()}" + (" (DUE within the window)" if due in window else " (not in the window)")
        else:
            due, report = None, "no business-day deadline stated in MANDATE.md"
        lines += [f"## {m}", f"Stale watchlist entries (over {h['stale_days']} days): "
                  + (", ".join(f"{short(g['bdc'])} (added {g['added']})" for g in stale) or "none"),
                  f"Reporting date: {report}.", ""]
        found[m] = {"stale": [g["bdc"] for g in stale], "due": due.isoformat() if due in window else None}
        out.decision({"ts": now.isoformat(), "job": "hygiene", "mandate": m, "action": "hygiene",
                      "stale": found[m]["stale"], "due": found[m]["due"]})
    out.report("hygiene", f"{today.isoformat()}.md", "\n".join(lines))
    return found
