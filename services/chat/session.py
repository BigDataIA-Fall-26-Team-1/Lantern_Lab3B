"""Part 0: authenticate Priya and open a session bound to ONE mandate.

Shared by the CLI and the web chat. It only wires kit functions together:
channel.login checks the session token; workspace.load_parts reads only the
active mandate's files from agent/; workspace.assemble applies the budgets
from params.yaml and reports any clipping.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from creditwatch import channel, skills, workspace

ROOT = Path(__file__).resolve().parents[2]


class SkillsTampered(Exception):
    """Start-up refused: a skill folder differs from skills.lock, is not in it, or
    the lockfile is missing (tutorial slide 23)."""


@dataclass
class OpenSession:
    session: channel.Session   # principal + the ACTIVE mandate
    client: str                # the active mandate's client name
    context: str               # the assembled context for this mandate only
    report: dict               # per part: used, budget, clipped


def load_config(root=ROOT):
    """params.yaml, users and mandates, as the notebooks load them."""
    root = Path(root)
    P = yaml.safe_load((root / "params.yaml").read_text())
    users = json.loads((root / P["data"]["fixture_dir"] / "users.json").read_text())
    mandates = json.loads((root / P["data"]["mandates"]).read_text())
    return P, users, mandates


def open_session(principal, token, mandate, root=ROOT):
    """Raises SkillsTampered if any skill differs from its pin, and PermissionError
    on bad credentials or a mandate the principal does not serve. The session
    stays bound to `mandate`; switching clients means opening a new session."""
    root = Path(root)
    P, users, mandates = load_config(root)
    ws = root / P["agent"]["workspace"]
    lockfile = ws / "skills" / "skills.lock"
    try:                                       # refuse to start on a tampered or unlocked skill
        skills.verify(ws / "skills", json.loads(lockfile.read_text()))
    except FileNotFoundError:
        raise SkillsTampered("no skills.lock") from None
    except RuntimeError as e:
        raise SkillsTampered(str(e)) from None
    s = channel.login(principal, token, mandate, users)
    idx = skills.skill_index(skills.load_skills(ws / "skills"))
    mem = ws / "mandates" / s.mandate / "MEMORY.md"           # this mandate's compacted memory only
    parts = workspace.load_parts(ws, s.mandate, skills_index=idx,
                                 memory=mem.read_text(encoding="utf-8") if mem.exists() else "")
    context, report = workspace.assemble(parts, P["context"]["budgets"])
    return OpenSession(s, mandates[s.mandate]["client"], context, report)


def report_lines(report):
    """The context report as text lines, for the CLI and the web page."""
    out = [f"{k:13s} {r['used']:4d} / {r['budget']:4d} tokens"
           + ("  CLIPPED" if r["clipped"] else "") for k, r in report.items()]
    out.append(f"{'total':13s} {sum(r['used'] for r in report.values()):4d} tokens")
    return out
