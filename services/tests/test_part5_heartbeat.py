"""Part 5 tests: our pairing, its precision and recall, the traps, coverage, the hand-check
sampler, and (later steps) the heartbeat jobs. Counts come from the fixture's ground truth and
mandates, never from hand-typed numbers.

Run from the repo root:  PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q
"""
import importlib.util

import pandas as pd
import pytest

from creditwatch import evals, panel, policy, signals
from services.chat.session import ROOT, load_config
from services.signals.pairing import normalise, pair, unpaired

P, USERS, MANDATES = load_config(ROOT)
M = P["heartbeat"]["materiality"]
FX = ROOT / P["data"]["fixture_dir"]


def fixture_sets():
    """The published fixture and the public structural variants 1 to 3 (as the self-check builds them)."""
    spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
    mf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mf)
    sets = [("fixture", panel.load_panel(FX / "panel_2p.csv"), pd.read_csv(FX / "ground_truth.csv", dtype=str))]
    for s in (1, 2, 3):
        rows, truth = mf.build(s)
        p = pd.DataFrame(rows)
        for c in panel.NUMERIC:
            if c in p:
                p[c] = pd.to_numeric(p[c], errors="coerce")
        sets.append((f"variant {s}", p, pd.DataFrame(truth).astype(str)))
    return sets


SETS = fixture_sets()
FIXTURE = SETS[0][1]
TRUTH = SETS[0][2]


# ---- 1: precision and recall, on the fixture and every public variant
@pytest.mark.parametrize("label,p,truth", SETS, ids=[s[0] for s in SETS])
def test_our_pairing_finds_every_planted_event_with_no_false_alarm(label, p, truth):
    base = evals.signal_scores(signals.find_signals(p, M), truth)
    ours = evals.signal_scores(signals.find_signals(p, M, pair=pair), truth)
    print(f"\n{label}: baseline P {base['precision']:.2f} R {base['recall']:.2f} | "
          f"ours P {ours['precision']:.2f} R {ours['recall']:.2f} by rule {ours['by_rule']}")
    assert ours["fp"] == 0 and ours["fn"] == 0 and ours["tp"] == len(truth)
    assert ours["recall"] > base["recall"]                       # better than the baseline


# ---- 2: the traps a careless fix falls into (tutorial slide 29), on the fixture
def _sig():
    return signals.find_signals(FIXTURE, M, pair=pair)


def test_granite_peaks_two_tranches_stay_separate_and_only_the_term_loan_is_marked_down():
    pairs = pair(FIXTURE)
    gp = pairs[pairs["identifier"].str.contains("Granite Peak")]
    assert len(gp) == 2 and set(gp["identifier"].map(normalise)) == {
        normalise(x) for x in gp["identifier_prior"]}                       # each paired with its own tranche
    assert all(("Delayed Draw" in c) == ("Delayed Draw" in pr) for c, pr in zip(gp["identifier"], gp["identifier_prior"]))
    marked = _sig()[_sig()["identifier"].str.contains("Granite Peak")]
    assert len(marked) == 1 and "Delayed Draw" not in marked.iloc[0]["identifier"]


def test_redwood_valleys_4_5_point_fall_is_not_a_signal():
    assert not _sig()["identifier"].str.contains("Redwood Valley").any()
    rv = pair(FIXTURE)
    rv = rv[rv["identifier"].str.contains("Redwood Valley")].iloc[0]
    drop = 100 * (rv["fair_value_prior"] / rv["principal_prior"] - rv["fair_value"] / rv["principal"])
    assert 4 < drop < M["markdown_points"]                               # paired, but under the threshold


def test_equity_has_no_price_and_thornburys_pik_at_origination_is_not_a_signal():
    s = _sig()
    assert not s["identifier"].str.contains("Preferred Equity|Common Stock").any()
    assert not s["identifier"].str.contains("Thornbury").any()


def test_an_ambiguous_collision_is_left_unpaired_not_guessed():
    """Golf's identifiers carry the all-in rate, so only the normalised stage can pair them. Add a
    second current Cedar Ridge row that normalises to the same key: one prior, two current."""
    p = FIXTURE.copy()
    row = p[(p["period_role"] == "current") & p["identifier"].str.contains("Cedar Ridge")].iloc[0].copy()
    row["source_rows"], row["identifier"] = "199", row["identifier"].replace("(9.80%)", "(9.95%)")
    p = pd.concat([p, row.to_frame().T], ignore_index=True)
    for c in panel.NUMERIC:
        if c in p:
            p[c] = pd.to_numeric(p[c], errors="coerce")
    pairs = pair(p)
    assert not pairs["identifier"].str.contains("Cedar Ridge").any()     # neither current row is guessed
    assert {"102", "199"} <= set(unpaired(p, pairs)["source_rows"])
    s = evals.signal_scores(signals.find_signals(p, M, pair=pair), TRUTH)
    assert s["fp"] == 0 and s["fn"] == 1                                 # a miss, never a false alarm


# ---- 3: coverage, per filing
def test_coverage_every_debt_holding_is_paired_except_the_genuinely_new_loan():
    report = signals.pairing_report(FIXTURE, pair(FIXTURE)).set_index("name")
    assert (report["paired"] + report["unpaired"] == report["debt_holdings"]).all()
    up = unpaired(FIXTURE, pair(FIXTURE))
    assert list(up["identifier"].str.contains("Thornbury")) == [True]   # the only unpaired debt holding
    assert report.loc["FIXTURE INDIA CREDIT FUND", "unpaired"] == 1
    assert report.drop("FIXTURE INDIA CREDIT FUND")["unpaired"].sum() == 0
    base = signals.pairing_report(FIXTURE, signals.pair_exact(FIXTURE)).set_index("name")
    assert base.loc["FIXTURE GOLF CAPITAL CORP", "paired"] == 0          # the baseline paired none of Golf


# ---- 9: the hand-check sampler is reproducible and leaves the judgement to a person
def test_the_hand_check_sample_is_reproducible(tmp_path):
    from services.signals.sample import COLS, main, sample
    a, b = sample(FIXTURE), sample(FIXTURE)
    assert a.equals(b) and list(a.columns) == COLS
    pairs = a[a["kind"] == "pair"]
    n_norm = (pair(FIXTURE)["match"] == "normalised").sum()             # counted from the data
    assert len(pairs) == 30 and (pairs["match"] == "normalised").sum() == min(n_norm, 15)
    assert list(a[a["kind"] == "unpaired"]["identifier"].str.contains("Thornbury")) == [True]   # only 1 exists
    assert (a["correct"] == "").all()                                    # a person fills it in
    assert not sample(FIXTURE, seed=1).equals(a)
    printed = []
    main([str(FX / "panel_2p.csv"), str(tmp_path / "sample.csv")], out=printed.append)
    assert pd.read_csv(tmp_path / "sample.csv").shape[0] == 31 and "30 pairs and 1 unpaired" in printed[0]


# ---- 4 and 5: the heartbeat jobs, on scratch copies (counts from ground truth, mandates and the feed)
import json  # noqa: E402

from creditwatch import clock, heartbeat  # noqa: E402
from services.heartbeat import brief_job, edgar_job, hygiene_job, release_job  # noqa: E402
from services.mcp.server import others_of  # noqa: E402

AT = clock.clock()                                             # 2026-10-15 06:00 New York, the fixture morning


def at(hour, minute=0, day=None):
    t = AT.replace(hour=hour, minute=minute)
    return t.replace(day=day) if day else t


def expected_signals():
    """Planted events each mandate should receive: those whose BDC it holds."""
    return {m: int(TRUTH["name"].isin(v["positions"]).sum()) for m, v in MANDATES.items()}


def expected_filings():
    """Watched filings per mandate, from the recorded feed, the registry and the positions."""
    recent = json.loads((FX / "edgar_recent.json").read_text())
    reg = json.loads((ROOT / P["data"]["registry"]).read_text())
    empty = {"form": [], "items": [], "accessionNumber": [], "acceptanceDateTime": []}
    n = {bdc: len(heartbeat.new_filings(recent.get(cik, empty), seen=set())) for bdc, cik in reg["bdc_cik"].items()}
    return {m: sum(n[b] for b in v["positions"]) for m, v in MANDATES.items()}, \
           {m: sum(k for b, k in n.items() if b not in v["positions"]) for m, v in MANDATES.items()}


def jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def alerts(root):
    return jsonl(root / "outputs" / "heartbeat" / "alerts.jsonl")


def test_the_release_at_six_is_routed_to_holders_and_held_for_the_brief(scratch_root):
    r = release_job(scratch_root, at(6))
    want = expected_signals()
    assert r["published"] and r["release"] == "2026_09"
    assert {m: len(v) for m, v in r["delivered"].items()} == want          # e.g. 8 and 4 on the fixture
    assert all(a == "hold" for v in r["delivered"].values() for a in v)     # 06:00 is quiet hours
    al = alerts(scratch_root)
    assert len(al) == sum(want.values())
    assert all(a["bdc"] in MANDATES[a["mandate"]]["positions"] for a in al)
    hotel = [a["mandate"] for a in al if a["bdc"] == "FIXTURE HOTEL SENIOR LENDING"]
    assert sorted(set(hotel)) == ["mandate_a", "mandate_b"]                 # Hotel reaches both holders

    again = release_job(scratch_root, at(6, 5))                             # the next release is not out
    assert again == {"release": "2026_10", "published": False, "delivered": {}}
    state = scratch_root / ".cache" / "heartbeat" / "state.json"
    s = json.loads(state.read_text()); s["last_release"] = None; state.write_text(json.dumps(s))
    assert release_job(scratch_root, at(9))["delivered"] == {}              # same release again: deduplicated
    assert len(alerts(scratch_root)) == sum(want.values())


def test_edgar_filings_alert_holders_and_log_not_material_for_the_others(scratch_root):
    held, not_held = expected_filings()
    r = edgar_job(scratch_root, at(9))
    assert {m: len(v) for m, v in r["delivered"].items() if v} == {m: n for m, n in held.items() if n}
    assert all(a == "alert" for v in r["delivered"].values() for a in v)    # 09:00 is not quiet
    al = alerts(scratch_root)
    assert all(a["bdc"] in MANDATES[a["mandate"]]["positions"] for a in al)
    nm = [d for d in jsonl(scratch_root / "outputs" / "heartbeat" / "decisions.jsonl") if d["action"] == "not_material"]
    assert {m: sum(d["mandate"] == m for d in nm) for m in MANDATES} == not_held
    assert "0009000011-26-000110" not in json.dumps(al)                      # the Item 5.02 8-K is not watched
    assert edgar_job(scratch_root, at(9, 30))["delivered"] == {}            # never delivered twice


def test_edgar_filings_in_quiet_hours_are_held(scratch_root):
    r = edgar_job(scratch_root, at(6, 30))
    assert r["delivered"] and all(a == "hold" for v in r["delivered"].values() for a in v)


def test_the_brief_holds_each_clients_own_items_only(scratch_root):
    release_job(scratch_root, at(6))
    edgar_job(scratch_root, at(6, 30))
    b = brief_job(scratch_root, at(7, 30))
    held_f, _ = expected_filings()
    want = {m: n + held_f[m] for m, n in expected_signals().items()}
    assert b["briefs"] == want
    for m in MANDATES:
        text = (scratch_root / "outputs" / "heartbeat" / "briefs" / f"2026-10-15-{m}.md").read_text()
        o = others_of(scratch_root, P, MANDATES, m)
        assert not (policy.mentions(text, o["names"]) or policy.leaks(text, o["terms"]))
        assert all(f"## {short}" in text for short in [x.replace("FIXTURE ", "").title().replace("Bdc", "BDC")
                                                      for x in MANDATES[m]["positions"]])
        assert text.count("[0009") == want[m]                               # every item cites its filing
        other_only = set(MANDATES["mandate_b" if m == "mandate_a" else "mandate_a"]["positions"]) - set(MANDATES[m]["positions"])
        assert not any(x.replace("FIXTURE ", "").title() in text for x in other_only)
    assert brief_job(scratch_root, at(7, 45))["briefs"] == {m: 0 for m in MANDATES}   # nothing briefed twice


def test_no_brief_on_a_day_without_a_release_or_at_the_weekend(scratch_root):
    assert brief_job(scratch_root, at(7, 30))["briefs"] == {}               # no release seen today
    release_job(scratch_root, at(6))
    assert brief_job(scratch_root, at(7, 30, day=17))["briefs"] == {}       # Saturday 17 October


def test_hygiene_flags_stale_watchlist_entries_and_reporting_dates_in_the_window(scratch_root):
    h = hygiene_job(scratch_root, at(8))                                    # Thursday 15 October
    assert h["mandate_a"] == {"stale": ["FIXTURE GOLF CAPITAL CORP"], "due": None}    # added 2026-07-14
    assert h["mandate_b"] == {"stale": [], "due": None}                     # no deadline stated
    monday = AT.replace(month=11, day=9, hour=8)                            # Monday 9 November
    assert hygiene_job(scratch_root, monday)["mandate_a"]["due"] == "2026-11-13"     # 10th business day
    assert (scratch_root / "outputs" / "heartbeat" / "hygiene" / "2026-11-09.md").exists()


def test_alert_records_have_every_field_and_pass_the_kits_checks(scratch_root):
    from creditwatch.check import ALERT_FIELDS, run_checks
    release_job(scratch_root, at(6)); edgar_job(scratch_root, at(9))
    al = alerts(scratch_root)
    assert al and all(set(ALERT_FIELDS) <= set(a) for a in al)
    assert all(a["citations"] and a["text"] for a in al)
    for a in al:                                                            # nothing of the other client
        o = others_of(scratch_root, P, MANDATES, a["mandate"])
        assert not (policy.mentions(json.dumps(a), o["names"]) or policy.leaks(json.dumps(a), o["terms"]))
    res = {name: ok for name, ok, _ in run_checks(scratch_root)}
    assert res["outputs/heartbeat/alerts.jsonl: one alert per line, all fields"]
    assert res["every alert goes only to a mandate that holds its BDC"]
    events = jsonl(scratch_root / "logs" / "audit" / "2026-10-15.jsonl")
    assert {e["kind"] for e in events} >= {"alert_held", "alert_sent"}
    assert not any("text" in e for e in events)                             # ids and hashes only
