"""Deterministic step of signal-explainer: signal(s) (JSON) -> one exact sentence each.

    python explain_signal.py signal.json      # one signal object, or a list of them

Prices in points, PIK in percentage points, extensions in months, and every
sentence cites the filing and both rows. Same input, same output.
"""
import json
import sys
from datetime import date


def short(name):
    """'FIXTURE JULIETT LENDING BDC' -> 'Juliett Lending BDC'."""
    return " ".join(w if w == "BDC" else w.title() for w in name.replace("FIXTURE ", "").split())


def holding(identifier):
    """The borrower when the identifier is 'type | borrower | terms', else the identifier."""
    parts = identifier.split(" | ")
    return parts[1] if len(parts) >= 3 else identifier


def months(d0, d1):
    return round((date.fromisoformat(d1) - date.fromisoformat(d0)).days / 30.44)


def explain(s):
    b, a = s["before"], s["after"]
    if s["rule"] == "MARKDOWN":
        what = f"marked down {b - a:.1f} points ({b:.1f} to {a:.1f})"
    elif s["rule"] == "PIK_UP":
        what = f"PIK up {a - b:.2f} percentage points ({b:.2f}% to {a:.2f}%)"
    elif s["rule"] == "EXTENDED":
        what = f"maturity moved out {months(b, a)} months ({b} to {a})"
    else:
        raise ValueError(f"unknown rule {s['rule']!r}")
    cite = f"[{s['adsh']}, row {s['row']}, prior row {s['prior_row']}]"
    return f"{short(s['name'])}: {holding(s['identifier'])} {what} {cite}"


if __name__ == "__main__":
    data = json.load(open(sys.argv[1]))
    for s in data if isinstance(data, list) else [data]:
        print(explain(s))
