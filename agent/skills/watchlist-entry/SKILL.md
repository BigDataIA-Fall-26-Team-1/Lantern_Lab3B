---
name: watchlist-entry
description: Draft a watchlist entry for a BDC the ACTIVE mandate holds, with the signal that justifies it. Use when asked to add a BDC to a client's watchlist.
---
# Steps
1. Call mandate_profile with the active mandate's scope; the BDC must be one of its positions.
2. Call bdc_signals with scope `public` and keep this BDC's signals as the evidence.
3. Write the reason from those signals alone: price changes in points, PIK in percentage
   points, each citing the accession number.
4. Use the exact line format `- BDC NAME: reason (added YYYY-MM-DD)`, dated from the clock.
5. request_approval for the exact entry, then watchlist_write with Priya's token. Clients
   rely on the watchlist: never write an entry without approval of that exact payload.
