"""Part 4: the compaction job, with a diff for review.

    PYTHONPATH=src python -m services.memory.compact --mandate mandate_a            # dry run: diff only
    PYTHONPATH=src python -m services.memory.compact --mandate mandate_a --apply    # writes MEMORY.md

Log lines older than --before (default: today on the injected clock) are
summarised into the mandate's MEMORY.md by the kit's memory.compact; every
line keeps its source ("[log/2026-10-01.md#1]"). The dry run compacts a
temporary copy, so it shows exactly what --apply would change and writes
nothing. Only the one mandate named is ever read or written.
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from creditwatch import clock, memory
from services.chat.session import ROOT, load_config


def compact(ws, mandate, before_day, apply=False):
    """Returns the unified diff of MEMORY.md. Writes it only when apply=True."""
    src = Path(ws) / "mandates" / mandate
    if apply:
        return memory.compact(Path(ws) / "mandates", mandate, before_day)
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / mandate
        shutil.copytree(src / "log", dst / "log")
        if (src / "MEMORY.md").exists():
            shutil.copy(src / "MEMORY.md", dst / "MEMORY.md")
        return memory.compact(tmp, mandate, before_day)


def main(argv=None, root=ROOT, out=print):
    P, _, mandates = load_config(root)
    ap = argparse.ArgumentParser(description="Compact one mandate's log into MEMORY.md")
    ap.add_argument("--mandate", required=True, choices=sorted(mandates))
    ap.add_argument("--before", default=clock.clock().date().isoformat(),
                    help="compact log days before this date (default: today on LANTERN_CLOCK)")
    ap.add_argument("--apply", action="store_true", help="write MEMORY.md (default: dry run, diff only)")
    a = ap.parse_args(argv)
    ws = Path(root) / P["agent"]["workspace"]
    diff = compact(ws, a.mandate, a.before, apply=a.apply)
    target = f"{P['agent']['workspace']}/mandates/{a.mandate}/MEMORY.md"
    for line in diff:
        out(line)
    if not diff:
        out(f"No change: {target} is already up to date.")
    elif a.apply:
        out(f"Written: {target}")
    else:
        out(f"Dry run: nothing written. Review the diff, then add --apply to write {target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
