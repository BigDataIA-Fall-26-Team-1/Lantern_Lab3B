"""Part 0 tests: login by session token, one mandate per session, isolation.

Run from the repo root:  PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from creditwatch import evals, policy  # noqa: E402
from services.chat.session import load_config, open_session  # noqa: E402

TOKEN = "priya-fixture-token"          # fixture only (tutorial slide 9)
P, USERS, MANDATES = load_config(ROOT)
CANARIES = json.loads((ROOT / P["data"]["fixture_dir"] / "canaries.json").read_text())
WS = ROOT / P["agent"]["workspace"]


# ---- 1 to 4: the session token authenticates Priya
def test_good_token_opens_a_session_bound_to_one_mandate():
    o = open_session("priya", TOKEN, "mandate_a")
    assert o.session.principal == "priya"
    assert o.session.mandate == "mandate_a"
    assert o.client == MANDATES["mandate_a"]["client"]


def test_wrong_token_is_refused():
    with pytest.raises(PermissionError, match="bad credentials"):
        open_session("priya", "guessed-token", "mandate_a")


def test_unknown_principal_is_refused():
    with pytest.raises(PermissionError, match="bad credentials"):
        open_session("mallory", TOKEN, "mandate_a")


def test_mandate_not_served_is_refused():
    with pytest.raises(PermissionError, match="no access"):
        open_session("priya", TOKEN, "mandate_c")


# ---- 5 and 6: an assembled context holds no other mandate's content or canaries
def _other_mandate_lines(active, other):
    """Lines of the other mandate's MANDATE.md and WATCHLIST.md that the active
    mandate's own files do not also contain (shared headings are not content)."""
    def lines(m):
        return {x.strip() for f in ("MANDATE.md", "WATCHLIST.md")
                for x in (WS / "mandates" / m / f).read_text(encoding="utf-8").splitlines() if x.strip()}
    return lines(other) - lines(active)


@pytest.mark.parametrize("active,other", [("mandate_a", "mandate_b"), ("mandate_b", "mandate_a")])
def test_context_holds_no_other_mandate_content_or_canaries(active, other):
    ctx = open_session("priya", TOKEN, active).context
    others = policy.confidential_terms(MANDATES, active)      # other client's name, aliases, terms
    assert evals.canary_hits([ctx], CANARIES[other]) == []    # catches the zero-width variant too
    assert policy.mentions(ctx, others["names"]) == []
    assert policy.leaks(ctx, others["terms"]) == []
    assert other not in ctx                                    # e.g. "mandate_b"
    assert [x for x in _other_mandate_lines(active, other) if x in ctx] == []
    assert evals.canary_hits([ctx], CANARIES[active])          # sanity: its own canary IS there
