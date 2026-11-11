"""Deterministic step of bdc-quarter-review: signals (JSON list) -> Markdown table.

    python signal_table.py signals.json
"""
import json
import sys

COLS = ["BDC", "Holding", "Signal", "Before", "After", "Accession", "Row"]


def fmt(v):
    """One table cell: numbers to 2 places; '|' escaped (identifiers contain it)."""
    s = f"{v:.2f}" if isinstance(v, float) else str(v)
    return s.replace("|", "\\|")


def short(name):
    return " ".join(w if w == "BDC" else w.title() for w in name.replace("FIXTURE ", "").split())


def table(signals):
    out = ["| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS)]
    for s in sorted(signals, key=lambda s: (s["name"], s["rule"], str(s["row"]))):
        cells = [short(s["name"]), s["identifier"], s["rule"],
                 s["before"], s["after"], s["adsh"], s["row"]]
        out.append("| " + " | ".join(fmt(c) for c in cells) + " |")
    return "\n".join(out)


if __name__ == "__main__":
    print(table(json.load(open(sys.argv[1]))))
