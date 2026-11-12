"""Part 5: a reproducible hand-check sample of our pairing (tutorial slide 29: hand-check at
least 30 pairs and 10 unpaired holdings on your real panel).

    PYTHONPATH=src python -m services.signals.sample PANEL OUT.csv [--seed 0]

PANEL is a two-period panel (.csv or .parquet). OUT.csv has one row per sampled pair or unpaired
holding, with an empty `correct` column and `note` for the person checking. Normalised pairs
(identifier text differs between the periods) are the ones most worth checking, so up to half
of the pairs are drawn from them. The same panel and seed always give the same sample.
"""
import argparse
import random
import sys
from pathlib import Path

import pandas as pd

from creditwatch import panel as kit_panel
from services.signals.pairing import pair, unpaired

COLS = ["kind", "match", "adsh", "name", "identifier", "identifier_prior", "source_rows", "source_rows_prior",
        "principal", "principal_prior", "maturity", "maturity_prior", "correct", "note"]


def sample(p, n_pairs=30, n_unpaired=10, seed=0):
    pairs = pair(p).sort_values(["adsh", "source_rows"]).reset_index(drop=True)
    rng = random.Random(seed)
    norm = pairs[pairs["match"] == "normalised"].index.tolist()
    exact = pairs[pairs["match"] == "exact"].index.tolist()
    take = rng.sample(norm, min(len(norm), n_pairs // 2))
    take += rng.sample(exact, min(len(exact), n_pairs - len(take)))
    picked = pairs.loc[sorted(take)].assign(kind="pair")
    up = unpaired(p, pairs).sort_values(["adsh", "source_rows"]).reset_index(drop=True)
    up = up.loc[sorted(rng.sample(range(len(up)), min(len(up), n_unpaired)))].assign(kind="unpaired", match="")
    out = pd.concat([picked, up], ignore_index=True)
    for c in COLS:
        if c not in out:
            out[c] = ""
    return out[COLS].astype(object).where(out[COLS].notna(), "")


def load(path):
    path = Path(path)
    return pd.read_parquet(path) if path.suffix == ".parquet" else kit_panel.load_panel(path)


def main(argv=None, out=print):
    ap = argparse.ArgumentParser(description="Draw a hand-check sample of pairs and unpaired holdings")
    ap.add_argument("panel"); ap.add_argument("out"); ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    s = sample(load(a.panel), seed=a.seed)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    s.to_csv(a.out, index=False)
    n = s["kind"].value_counts().to_dict()
    out(f"{n.get('pair', 0)} pairs and {n.get('unpaired', 0)} unpaired holdings -> {a.out}  "
        "(fill in `correct` with yes or no, and a `note` for every no)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
