---
name: release-brief
description: Brief Priya on what a new SEC release changed in the BDCs the ACTIVE mandate holds, with citations. Use on release mornings.
---
# Steps
1. Call mandate_profile with the active mandate's scope for the BDCs it holds.
2. Call bdc_signals with scope `public`; keep only signals for those BDCs.
3. For each held BDC, state coverage: debt holdings paired with the prior schedule, and
   unpaired (an unpaired holding cannot produce a signal).
4. One line per signal, grouped by BDC: price changes in points, each citing the accession.
5. The brief is for Priya only, never emailed to the client; the client version is the
   client-monthly-note skill.
