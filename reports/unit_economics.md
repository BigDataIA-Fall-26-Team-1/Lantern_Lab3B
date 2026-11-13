# Unit economics: what a release morning costs

| Line | Credit Watch |
|---|---|
| Cost per completed task | $0.00512 per client note (4 model steps at $0.00128); the stand-in model's tokens (2,000 in / 300 out per step) at params.yaml's illustrative prices; Part 2 measures a real model |
| Latency | release published to brief ready: 90 minutes; filing accepted to alert: within one 30-minute poll (live) |
| Human time saved | 4 BDC reviews per release at 3 hours, $120 an hour (ASSUMPTION): x0.5: 6 h = $720; x1: 12 h = $1,440; x2: 24 h = $2,880 |
| Quality | signals: fixture P 1.00 R 1.00; variant 1 P 1.00 R 1.00; variant 2 P 1.00 R 1.00; variant 3 P 1.00 R 1.00; adherence: 4/4 rules held after learning; dev suite 8/8 pass |
| Cost of failure | a false alert costs $30.00 of analyst time (15 minutes, ASSUMPTION), 0 false alerts on the fixture; a leak is priced at $250,000 (**ASSUMPTION**, a placeholder); one client's position in another's letter is the costliest failure; approvals of the exact payload and the R-OTHER-MANDATE rule stop it before anything leaves the firm |
| Cost per mandate-month (mandate_a) | $0.0205: 4 model tasks x $0.00512 + idle $0.00; 1 brief (deterministic, $0) |
| Cost per mandate-month (mandate_b) | $0.0154: 3 model tasks x $0.00512 + idle $0.00; 1 brief (deterministic, $0) |

**The case is coverage.** Model cost is small. What justifies the agent is that it watches every held BDC in every release, for every client, which no analyst does by hand; the heartbeat's idle cost is $0.00 per mandate-month because it makes no model calls.

## Assumptions (stated, not measured; from params.yaml)
- Tasks per mandate-month (one monthly release): client note 1, bdc review per held bdc 1, brief 1.
- Analyst hours per BDC review: 3; rate $120 an hour (shown at x0.5, x1 and x2).
- A false alert costs 15 minutes of Priya's time.
- **ASSUMPTION: one leak costs $250,000.** A placeholder for legal review, client remediation and relationship risk; replace it with your own figure and reasoning.
- A BDC review is costed as one note-sized task. Prices are params.yaml's illustrative prices, and the model is the stand-in until Part 2.
