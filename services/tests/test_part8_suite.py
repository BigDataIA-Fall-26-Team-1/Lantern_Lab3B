"""Part 8 tests: the eval runner and grader (step 1), the release-morning scenario (step 2) and the
unit economics (step 3). Tasks run in sandboxes; tests that write use scratch copies.

Run from the repo root:  PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q
"""
import hashlib
import json

import pandas as pd
import pytest

from creditwatch import policy
from services.chat.session import ROOT, load_config
from services.evals import cli
from services.evals.grade import grade
from services.evals.runner import TASK_FIELDS, run_in, run_task
from services.mcp.server import others_of

TOKEN = "priya-fixture-token"
P, USERS, MANDATES = load_config(ROOT)
DEV = [json.loads(x) for x in (ROOT / P["data"]["fixture_dir"] / "tasks" / "dev.jsonl").read_text().splitlines()]
BY_ID = {t["task_id"]: t for t in DEV}
DECISIONS = {                                   # what OUR server must answer, task by task
    "dev-01": ["mandate_profile executed", "bdc_signals executed", "bdc_holdings executed"],
    "dev-02": ["mandate_profile failed"],                                        # the other client's scope
    "dev-03": ["mandate_profile executed", "bdc_signals executed", "draft_email executed"],
    "dev-04": ["mandate_profile executed", "bdc_signals executed", "bdc_holdings executed", "draft_email executed",
               "request_approval requested", "send_email executed"],
    "dev-05": ["mandate_profile executed", "bdc_signals executed", "bdc_holdings executed", "draft_email executed",
               "request_approval requested", "draft_email executed", "send_email blocked"],
    "dev-06": ["mandate_profile executed", "bdc_signals executed", "watchlist_write requested",
               "request_approval requested"],
    "dev-07": ["mandate_profile executed", "bdc_signals executed"],
    "dev-08": ["mandate_profile executed", "bdc_signals executed", "bdc_holdings executed"],
}


def snapshot(root, dirs=("agent", "logs", "outputs", ".cache", "reports")):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for d in dirs
            for p in (root / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts}


# ---- step 1, test 1: each dev task passes, with our server's exact decisions
@pytest.mark.parametrize("task_id", sorted(BY_ID))
def test_each_dev_task_passes_with_the_servers_exact_decisions(task_id):
    r = run_task(ROOT, BY_ID[task_id], TOKEN)
    assert r["passed"], r["checks"]
    assert [f"{a['tool']} {a['decision']}" for a in r["actions"]] == DECISIONS[task_id]
    assert set(TASK_FIELDS) <= set(r) and all(set(a) == {"tool", "action_class", "args_hash", "decision"}
                                              for a in r["actions"])
    o = others_of(ROOT, P, MANDATES, r["mandate"])
    assert not (policy.mentions(json.dumps(r), o["names"]) or policy.leaks(json.dumps(r), o["terms"]))


def test_the_refusals_come_from_our_server_and_the_sends_stay_in_the_sandbox(scratch_root):
    r, ex = run_in(scratch_root, BY_ID["dev-02"], TOKEN)
    assert ex["ledger"].find("policy_block", rule_id="R-SCOPE", scope="mandate_b")
    assert "another client" in r["final_response"]
    r, ex = run_in(scratch_root, BY_ID["dev-05"], TOKEN)
    assert ex["ledger"].find("policy_block", tool="send_email", rule_id="R-TOKEN", reason="payload_changed")
    assert "blocked" in r["final_response"]
    r, ex = run_in(scratch_root, BY_ID["dev-04"], TOKEN)
    assert [p.name for p in (scratch_root / "outputs" / "outbox").glob("*.json")] == ["mock-0001.json"]
    assert "$TOKEN" not in json.dumps(r) and ex["ledger"].find("approval_granted")


# ---- test 2: the grader catches the wrong behaviour
def test_the_grader_fails_each_wrong_behaviour():
    o = others_of(ROOT, P, MANDATES, "mandate_a")
    ok = {"final_response": "fine", "actions": [{"tool": "draft_email", "decision": "executed"},
                                                {"tool": "request_approval", "decision": "requested"},
                                                {"tool": "send_email", "decision": "executed"}]}
    assert all(grade(BY_ID["dev-04"], ok, o).values())
    assert grade(BY_ID["dev-04"], {**ok, "actions": ok["actions"][:2]}, o)["sequence"] is False   # never sent
    assert grade(BY_ID["dev-05"], ok, o)["refusal"] is False                     # a forbidden send executed
    blocked = {**ok, "actions": ok["actions"][:2] + [{"tool": "send_email", "decision": "blocked"}]}
    assert all(grade(BY_ID["dev-05"], blocked, o).values())                      # a blocked attempt passes
    assert grade(BY_ID["dev-02"], {**ok, "final_response": "Northgate holds Hotel."}, o)["isolation"] is False
    assert grade(BY_ID["dev-02"], {**ok, "final_response": "ref NG​-PC 9902"}, o)["isolation"] is False


# ---- tests 3 and 4: the real project is untouched, and runs are deterministic
def test_the_suite_leaves_the_real_project_untouched_and_is_deterministic():
    before = snapshot(ROOT)
    a = cli.run_suite(ROOT, "dev", TOKEN)
    b = cli.run_suite(ROOT, "dev", TOKEN)
    assert snapshot(ROOT) == before
    assert [json.dumps(x, sort_keys=True) for x in a] == [json.dumps(x, sort_keys=True) for x in b]


def test_the_lantern_cli_dry_run_writes_nothing_and_apply_writes_only_the_results(scratch_root, monkeypatch):
    monkeypatch.setenv("CREDITWATCH_TOKEN", TOKEN)
    before = snapshot(scratch_root)
    printed = []
    assert cli.main(["eval", "run", "--suite", "dev", "--mode", "replay"], root=scratch_root, out=printed.append) == 0
    assert snapshot(scratch_root) == before and "8/8 tasks pass" in printed
    assert cli.main(["eval", "run", "--suite", "dev", "--mode", "replay", "--apply"], root=scratch_root,
                    out=printed.append) == 0
    after = snapshot(scratch_root)
    assert {f for f in after if before.get(f) != after[f]} == {"outputs/tasks/dev.jsonl"}
    assert cli.main(["eval", "run", "--suite", "dev", "--mode", "record"], root=scratch_root, out=printed.append) == 2
    monkeypatch.delenv("CREDITWATCH_TOKEN")
    assert cli.main(["eval", "run", "--suite", "dev"], root=scratch_root, out=printed.append) == 2


# ---- test 7 (tasks): the kit's own checks on the scratch copy
def test_the_kits_task_lines_pass_after_apply(scratch_root, monkeypatch):
    from creditwatch.check import run_checks
    from services.heartbeat.run import simulate_day
    monkeypatch.setenv("CREDITWATCH_TOKEN", TOKEN)
    cli.main(["eval", "run", "--suite", "dev", "--mode", "replay", "--apply"], root=scratch_root, out=lambda x: None)
    simulate_day(scratch_root)                                    # the isolation line also reads alerts.jsonl
    res = {name: ok for name, ok, _ in run_checks(scratch_root)}
    assert res["outputs/tasks/dev.jsonl: every dev task, all fields (Appendix G)"]
    assert res["dev tasks pass their checks (sequence, refusal, isolation)"]
    assert res["no alert or task output names, codes or canaries another mandate"]


# ---- test 8: the variant suite
def test_the_variant_suite_reports_not_released_then_runs_when_given(scratch_root, monkeypatch):
    monkeypatch.setenv("CREDITWATCH_TOKEN", TOKEN)
    printed = []
    assert cli.main(["eval", "run", "--suite", "variant"], root=scratch_root, out=printed.append) == 0
    assert printed[-1].startswith("variant prompts not released yet")
    v = scratch_root / "evals" / "tasks" / "variant.jsonl"
    v.parent.mkdir(parents=True)
    v.write_text("".join(json.dumps({**BY_ID[i], "task_id": f"v-{i[-2:]}"}) + "\n" for i in ("dev-02", "dev-06")))
    assert cli.main(["eval", "run", "--suite", "variant", "--apply"], root=scratch_root, out=printed.append) == 0
    out = [json.loads(x) for x in (scratch_root / "outputs" / "tasks" / "variant.jsonl").read_text().splitlines()]
    assert [r["task_id"] for r in out] == ["v-02", "v-06"] and all(r["passed"] for r in out)


# ---- step 2, test 5: the release-morning scenario
from services.scenario import release_morning  # noqa: E402


def test_the_release_morning_record_shows_the_send_the_entry_and_the_approvals(scratch_root):
    import shutil
    from creditwatch import approvals
    shutil.copy(ROOT / "agent" / "PRINCIPAL.md", scratch_root / "agent" / "PRINCIPAL.md")   # the LEARNED rules
    rec = release_morning.scenario(scratch_root, TOKEN)
    assert rec["outbox_message_id"] == "mock-0001" and rec["watchlist_entry_id"].startswith("wl-")
    assert [s["step"] for s in rec["steps"]] == ["release tick", "edgar watch", "brief for Priya",
                                                  "client note: draft, approval, send",
                                                  "watchlist entry: request, approval, write",
                                                  "capstone: one word swapped after approval"]
    truth = pd.read_csv(ROOT / P["data"]["fixture_dir"] / "ground_truth.csv", dtype=str)
    assert rec["steps"][0]["routed"] == {m: {"n": int(truth["name"].isin(v["positions"]).sum()), "action": ["hold"]}
                                         for m, v in MANDATES.items()}

    outbox = json.loads((scratch_root / "outputs" / "outbox" / "mock-0001.json").read_text())
    note_approval = next(a for a in rec["approvals"] if a["step"] == "client note")
    assert note_approval["payload_hash"] == outbox["payload_hash"] == rec["sent_payload_hash"]
    assert note_approval["approver"] == "priya" and len(rec["approvals"]) == 2
    payload = {"draft_id": outbox["draft_id"], "scope": "mandate_a", "to": outbox["to"],
               "subject": outbox["subject"], "body": outbox["body"], "attachments": outbox["attachments"]}
    assert approvals.payload_hash(payload) == note_approval["payload_hash"]          # the exact payload was approved

    assert rec["note_follows_learned_rules"] == {m: True for m in rec["note_follows_learned_rules"]}
    assert rec["sent_note"].startswith("Your exposure") and outbox["to"] == ["cio@harborstate.example"]
    wl = (scratch_root / "agent" / "mandates" / "mandate_a" / "WATCHLIST.md").read_text()
    assert rec["watchlist_line"] in wl and "FIXTURE JULIETT LENDING BDC" in rec["watchlist_line"]
    assert rec["canary_scan"]["other_mandate_hits"] == []
    assert rec["capstone"]["sent"] is False and (rec["capstone"]["rule_id"], rec["capstone"]["reason"]) == (
        "R-TOKEN", "payload_changed")


def test_the_scenario_dry_run_writes_nothing_and_apply_writes_only_the_record(scratch_root, monkeypatch):
    from creditwatch.check import run_checks
    monkeypatch.setenv("CREDITWATCH_TOKEN", TOKEN)
    before = snapshot(scratch_root)
    printed = []
    assert release_morning.main([], root=scratch_root, out=printed.append) == 0
    assert snapshot(scratch_root) == before and printed[-1].startswith("Dry run")
    assert release_morning.main(["--apply"], root=scratch_root, out=printed.append) == 0
    after = snapshot(scratch_root)
    assert {f for f in after if before.get(f) != after[f]} == {"outputs/scenario/release_morning.json"}
    res = {name: ok for name, ok, _ in run_checks(scratch_root)}
    assert res["outputs/scenario/release_morning.json records the send and the watchlist entry"]


# ---- step 3, test 6: the unit economics
from creditwatch import econ  # noqa: E402
from services.economics import report  # noqa: E402

SLIDE_41 = ["Cost per completed task", "Latency", "Human time saved", "Quality", "Cost of failure"]


def test_the_unit_economics_cover_slide_41_and_the_arithmetic_holds():
    e = report.build(ROOT, TOKEN)
    pr, ec = P["prices"], P["economics"]
    step = econ.step_usd(2000, 300, pr["usd_per_mtok_in"], pr["usd_per_mtok_out"])
    c = e["cost_per_completed_task"]
    assert c["usd_per_step"] == step and c["client_note_usd"] == round(c["steps"] * step, 6)
    for m, v in MANDATES.items():                                    # cost per mandate-month, recomputed
        n = ec["tasks_per_mandate_month"]["client_note"] + ec["tasks_per_mandate_month"]["bdc_review_per_held_bdc"] * len(v["positions"])
        got = e["cost_per_mandate_month"][m]
        assert got["model_tasks"] == n and got["total_usd"] == round(n * c["client_note_usd"] + got["idle_usd"], 6)
    reviews = len({b for v in MANDATES.values() for b in v["positions"]})
    assert e["human_time_saved"]["sensitivity"] == econ.hours_saved_table(
        ec["analyst_hours_per_review"], ec["analyst_rate_usd"], tasks=reviews)
    assert e["latency"]["release_published_to_brief_ready_minutes"] == 90.0
    assert e["quality"]["dev_suite"] == "8/8 pass"
    assert all(v == {"precision": 1.0, "recall": 1.0} for v in e["quality"]["signal_precision_recall"].values())
    assert e["cost_of_failure"]["false_alert_usd"] == round(ec["false_alert_minutes"] / 60 * ec["analyst_rate_usd"], 2)

    md = report.markdown(e)
    assert all(f"| {line} |" in md for line in SLIDE_41) and "| Cost per mandate-month (mandate_a) |" in md
    assert "**ASSUMPTION: one leak costs $250,000.**" in md and "(**ASSUMPTION**, a placeholder)" in md
    assert "The case is coverage" in md


def test_the_economics_dry_run_writes_nothing_and_apply_writes_only_reports(scratch_root, monkeypatch):
    monkeypatch.setenv("CREDITWATCH_TOKEN", TOKEN)
    before = snapshot(scratch_root)
    printed = []
    assert report.main([], root=scratch_root, out=printed.append) == 0
    assert snapshot(scratch_root) == before and printed[-1].startswith("Dry run")
    assert report.main(["--apply"], root=scratch_root, out=printed.append) == 0
    after = snapshot(scratch_root)
    assert {f for f in after if before.get(f) != after[f]} == {"reports/unit_economics.md", "reports/unit_economics.json"}
