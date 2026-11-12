"""Part 5: our pairing. Which current row is the same loan as which prior row, inside ONE filing.

Many filers write the coupon, the spread or the maturity into the identifier, so the text changes
when the loan does not (tutorial slides 27 to 29). Two stages, both one-to-one:

1. exact: same filing, identical identifier (the kit's baseline), when unique on both sides;
2. normalised: same filing, same investment type, and the same identifier once the volatile parts
   are removed (the all-in rate "(10.05%)", the spread "S + 5.75%", other percentages, dates such
   as "09/2030"). The tranche name stays, so a term loan never pairs with its delayed-draw sibling.

A key shared by two rows on either side is ambiguous: those rows stay UNPAIRED rather than be
guessed. A wrong pair is a false alarm, and precision is graded (slide 31).

Use it as   signals.find_signals(panel, materiality, pair=pair)
"""
import re

import pandas as pd

_VOLATILE = [
    re.compile(r"\(\s*\d+(?:\.\d+)?\s*%\s*\)"),                 # all-in rate in brackets: (10.05%)
    re.compile(r"\b(?:s|sofr|l|libor|p|prime|e|euribor)\s*\+\s*\d+(?:\.\d+)?\s*%"),   # spread: S + 5.75%
    re.compile(r"\d+(?:\.\d+)?\s*%"),                           # any other percentage (fixed coupons, PIK)
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}/\d{4}\b"),   # dates
]


def normalise(identifier):
    """The identifier without its volatile parts, lower case, punctuation collapsed."""
    s = str(identifier).lower()
    for rx in _VOLATILE:
        s = rx.sub(" ", s)
    return " ".join(re.sub(r"[^0-9a-z]+", " ", s).split())


def _unique(df, keys):
    """Rows whose key appears exactly once."""
    return df[~df.duplicated(keys, keep=False)]


def _one_to_one(cur, pri, keys):
    c, p = _unique(cur, keys), _unique(pri, keys)
    return c.merge(p, on=keys, suffixes=("", "_prior"))


def pair(panel):
    """Pairs of (current, prior) rows; same columns as the kit's pair_exact, plus
    identifier_prior and match ('exact' or 'normalised')."""
    cur = panel[panel["period_role"] == "current"].copy()
    pri = panel[panel["period_role"] == "prior"].copy()

    exact = _one_to_one(cur, pri, ["adsh", "identifier"])
    exact["identifier_prior"] = exact["identifier"]
    exact["match"] = "exact"

    taken_c = set(zip(exact["adsh"], exact["source_rows"]))
    taken_p = set(zip(exact["adsh"], exact["source_rows_prior"]))
    rc = cur[[(a, r) not in taken_c for a, r in zip(cur["adsh"], cur["source_rows"])]].copy()
    rp = pri[[(a, r) not in taken_p for a, r in zip(pri["adsh"], pri["source_rows"])]].copy()
    rc["_key"] = rc["identifier"].map(normalise)
    rp["_key"] = rp["identifier"].map(normalise)
    rp = rp.rename(columns={"identifier": "identifier_prior"})
    keys = ["adsh", "investment_type", "_key"]
    norm = _one_to_one(rc, rp, keys)
    norm = norm.rename(columns={"identifier_prior_prior": "identifier_prior"}).drop(columns="_key")
    norm["match"] = "normalised"
    return pd.concat([exact, norm], ignore_index=True)


def unpaired(panel, pairs):
    """Current DEBT holdings (principal > 0) that found no prior row."""
    cur = panel[(panel["period_role"] == "current") & (panel["principal"] > 0)]
    got = set(zip(pairs["adsh"], pairs["source_rows"]))
    return cur[[(a, r) not in got for a, r in zip(cur["adsh"], cur["source_rows"])]]
