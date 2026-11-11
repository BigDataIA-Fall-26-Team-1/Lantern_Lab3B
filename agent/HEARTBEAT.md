# HEARTBEAT: what runs unprompted

- release_watch_dag: daily 06:00 New York time. Is the next monthly SEC BDC release published?
  If so: refresh the two-period panel (your Case Study 2B pipeline), compute signals, route
  them to the mandates that hold each BDC, alert or hold.
- edgar_watch_dag: every 30 minutes. New 10-Q, 10-K or 8-K Item 2.02 from a held BDC: an
  alert, or a logged not-material decision.
- monthly_brief_dag: 07:30 on release days, weekdays: one brief per mandate, to Priya.
- hygiene_dag: Mondays 08:00: watchlist entries not reviewed in 30 days; client reporting
  dates in the next 5 business days.

Materiality and quiet hours (21:00 to 07:00) come from params.yaml, never from this file.
