---
name: bdc-quarter-review
description: Review one BDC's latest filing for credit signals (markdowns, new or increased PIK, maturity extensions), with coverage and citations. Use when asked what changed at a BDC.
---
# Steps
1. Call bdc_signals with scope `public` and keep the rows for the BDC.
2. State coverage: how many of its debt holdings were paired with the prior schedule, and
   how many were not (unpaired holdings cannot produce a signal).
3. Run `scripts/signal_table.py` on the signals for the table; do not retype numbers.
4. Two to four sentences on what matters, each citing the accession number.
5. Draft only. A review that goes to a client is an email, and needs approval.
