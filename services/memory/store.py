"""Part 4: one memory store per mandate, rebuilt from that mandate's own log.

The store is a rebuildable index (git-ignored under .cache/): the source of
truth is agent/mandates/<m>/log/*.md, which the agent only appends to. Each
mandate gets its own SQLite FTS5 file, so isolation is physical: a session
builds and opens only its own mandate's store.
"""
from pathlib import Path

from creditwatch import memory


def build_store(ws, mandate, cache_root):
    """Delete and rebuild ONE mandate's store from its log lines, each with its
    provenance ("log/2026-10-01.md#1"). Returns the kit's MandateStore."""
    (Path(cache_root) / mandate / "memory.sqlite").unlink(missing_ok=True)
    store = memory.MandateStore(cache_root, mandate)
    for f in sorted((Path(ws) / "mandates" / mandate / "log").glob("*.md")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            n, _, text = ln.partition(". ")
            if text:
                store.add(text, f"log/{f.name}#{n}")
    return store
