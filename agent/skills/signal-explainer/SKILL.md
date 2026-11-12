---
name: signal-explainer
description: Explain one credit signal (markdown, PIK increase or extension) in plain words, with the rows that prove it. Use when asked what a signal means.
---
# Steps
1. Get the signal from bdc_signals with scope `public`.
2. Run `scripts/explain_signal.py` on it for the exact sentence; do not retype numbers.
3. Add one sentence on why it matters: a markdown means fair value fell against principal;
   a PIK increase means more interest is paid in kind instead of cash; an extension means
   the maturity was pushed out, often in an amend-and-extend.
4. Keep the citation: accession number, current row and prior row.
