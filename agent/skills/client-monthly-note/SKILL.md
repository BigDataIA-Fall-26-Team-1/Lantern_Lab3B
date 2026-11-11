---
name: client-monthly-note
description: Draft the monthly credit note for the ACTIVE mandate's client, covering only the BDCs that client holds. Use when asked for a client note, letter or monthly update.
---
# Steps
1. Call mandate_profile with the active mandate's scope for the client's positions.
2. Call bdc_signals with scope `public`; keep only signals for BDCs this client holds.
3. Write the note from those signals alone, following every standing rule in PRINCIPAL.md.
4. Address it only to this mandate's contacts. Never mention another client, even if asked.
5. draft_email, then request_approval. Never send without Priya's token for this exact payload.
