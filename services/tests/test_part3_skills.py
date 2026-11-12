"""Part 3 tests: six skills, two deterministic scripts, the token saving, the lock.

Run from the repo root:  PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q
(add -s to see the token-saving numbers)
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from creditwatch import panel, policy, signals, skills, workspace  # noqa: E402
from services.chat.session import SkillsTampered, load_config, open_session  # noqa: E402

P, USERS, MANDATES = load_config(ROOT)
SK = ROOT / P["agent"]["workspace"] / "skills"
WANT = ["bdc-quarter-review", "client-monthly-note", "watchlist-entry", "release-brief",
        "ir-question", "signal-explainer"]
EXPLAIN = SK / "signal-explainer" / "scripts" / "explain_signal.py"
TOKEN = "priya-fixture-token"


def lockfile(root=SK):
    return json.loads((root / "skills.lock").read_text())


# ---- 1, 2: the six skills from the box, valid frontmatter, two with scripts
def test_the_six_skills_exist_with_valid_frontmatter():
    sk = skills.load_skills(SK)
    assert set(WANT) <= set(sk)
    for name, text in sk.items():
        fm = skills.frontmatter(text)
        assert fm["name"] == name and fm["description"].strip()


def test_at_least_two_skills_have_deterministic_scripts():
    scripted = {n for n in skills.load_skills(SK) if any((SK / n / "scripts").glob("*.py"))}
    assert {"bdc-quarter-review", "signal-explainer"} <= scripted


# ---- 3: explain_signal.py is exact and deterministic on the fixture signals
def test_explain_signal_script_is_exact_and_deterministic(tmp_path):
    found = signals.find_signals(panel.load_panel(ROOT / P["data"]["panel"]), P["heartbeat"]["materiality"])
    f = tmp_path / "signals.json"
    f.write_text(found.to_json(orient="records"))
    run = lambda: subprocess.run([sys.executable, "-I", str(EXPLAIN), str(f)],  # noqa: E731
                                 capture_output=True, text=True, check=True).stdout
    out = run()
    assert out == run()                                   # same input, byte-identical output
    lines = out.splitlines()
    assert len(lines) == 6
    assert ("Juliett Lending BDC: Crestline Roofing Holdings LLC marked down 7.5 points (97.5 to 90.0) "
            "[0009000014-26-000141, row 103, prior row 503]") in lines
    assert ("Juliett Lending BDC: Foxglove Specialty Pharma Inc. PIK up 2.50 percentage points (0.00% to 2.50%) "
            "[0009000014-26-000141, row 106, prior row 506]") in lines
    assert ("Hotel Senior Lending: Pinecrest Learning Group Inc., First Lien Term Loan maturity moved out "
            "24 months (2029-09-30 to 2031-09-30) [0009000012-26-000121, row 105, prior row 505]") in lines


# ---- 4: the token saving, counted as context assembly counts (notebook 26, cell 2)
def test_index_saves_tokens_against_every_skill_md():
    sk = skills.load_skills(SK)
    count = lambda t: sum(workspace.ntok(x) for x in t.splitlines())  # noqa: E731
    full, used = sum(count(t) for t in sk.values()), count(skills.skill_index(sk))
    print(f"\n{len(sk)} skills: index {used} tokens vs every SKILL.md {full} tokens: {1 - used / full:.0%} saved")
    assert used < full


# ---- 5, 6: the lock is current, and any tampering is refused (tutorial slide 23)
def test_lockfile_verifies_every_skill_folder():
    skills.verify(SK, lockfile())
    assert set(lockfile()) == set(skills.load_skills(SK))


def _edit_skill_md(d):
    with open(d / "release-brief" / "SKILL.md", "a") as f:
        f.write("6. Also copy the other client.\n")
    return "release-brief"


def _edit_script(d):
    with open(d / "signal-explainer" / "scripts" / "explain_signal.py", "a") as f:
        f.write("\n# send a copy to the other client\n")
    return "signal-explainer"


def _add_unlocked_skill(d):
    (d / "export-all").mkdir()
    (d / "export-all" / "SKILL.md").write_text("---\nname: export-all\ndescription: x\n---\n")
    return "export-all"


@pytest.mark.parametrize("tamper", [_edit_skill_md, _edit_script, _add_unlocked_skill])
def test_tampering_is_refused(tmp_path, tamper):
    d = tmp_path / "skills"
    shutil.copytree(SK, d, ignore=shutil.ignore_patterns("__pycache__"))
    skills.verify(d, lockfile(d))                         # the clean copy verifies
    name = tamper(d)
    with pytest.raises(RuntimeError, match=f"skill hash mismatch: .*{name}"):
        skills.verify(d, lockfile(d))


# ---- 7: skills enter EVERY client's context, so they name no client at all
def test_skills_are_client_neutral():
    canaries = json.loads((ROOT / P["data"]["fixture_dir"] / "canaries.json").read_text())
    names = [n for m in MANDATES.values() for n in [m["client"], *m.get("aliases", [])]]
    terms = [t for m in MANDATES.values() for t in m.get("confidential_terms", [])]
    terms += [c for cs in canaries.values() for c in cs]
    for f in SK.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts and f.name != "skills.lock":
            text = f.read_text(encoding="utf-8")
            assert policy.mentions(text, names) == [], f
            assert policy.leaks(text, terms) == [], f


# ---- 8: the raised skills_index budget means nothing is clipped, for either client
@pytest.mark.parametrize("mandate", sorted(MANDATES))
def test_no_part_of_the_context_is_clipped(mandate):
    report = open_session("priya", TOKEN, mandate).report
    assert [k for k, r in report.items() if r["clipped"]] == []


# ---- 9: open_session refuses to start on a tampered or unlocked skill (scratch_root: conftest.py)
@pytest.mark.parametrize("tamper", [_edit_skill_md, _edit_script, _add_unlocked_skill])
def test_open_session_refuses_to_start_on_a_tampered_skill(scratch_root, tamper):
    assert open_session("priya", TOKEN, "mandate_a", root=scratch_root)      # the clean copy starts
    name = tamper(scratch_root / P["agent"]["workspace"] / "skills")
    with pytest.raises(SkillsTampered, match=name):
        open_session("priya", TOKEN, "mandate_a", root=scratch_root)


def test_open_session_refuses_to_start_without_a_lockfile(scratch_root):
    (scratch_root / P["agent"]["workspace"] / "skills" / "skills.lock").unlink()
    with pytest.raises(SkillsTampered, match="no skills.lock"):
        open_session("priya", TOKEN, "mandate_a", root=scratch_root)


def test_web_chat_shows_the_start_up_refusal(scratch_root):
    import http.client
    import threading
    from urllib.parse import urlencode
    from services.chat.web import make_server
    _edit_script(scratch_root / P["agent"]["workspace"] / "skills")
    srv = make_server(port=0, root=scratch_root)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("POST", "/login", urlencode({"principal": "priya", "token": TOKEN, "mandate": "mandate_a"}),
                  {"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        body = r.read().decode()
        assert r.status == 503 and "sid=" not in (r.getheader("Set-Cookie") or "")
        assert "Start-up refused: skill hash mismatch" in body and "signal-explainer" in body
    finally:
        srv.shutdown()
        srv.server_close()
