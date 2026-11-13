# Lab 3B: the Your turn exercises (notebooks 23 to 31)

Every Your turn box is built. Results on the fixture:

- Kit self-check: **16/16** (`PYTHONPATH=src python -m creditwatch.check`)
- Kit tests: **36/36** (`python tests/run_tests.py`)
- Our tests: **160 passed** (`PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests -q`)
- Real model (Part 2): **5/8** dev tasks, reported separately in [reports/real_model_run.md](reports/real_model_run.md)

Setup for the commands below, from the repo root:

```
source .venv/bin/activate
read -s CREDITWATCH_TOKEN && export CREDITWATCH_TOKEN     # the fixture token; never stored in a file
```

Commands marked "dry run" write nothing. Commands that write graded outputs take `--apply`.

## Notebook 23: harness, CLI and web chat (Part 0)

- **Built:** Priya logs in with her session token, and each session is bound to one mandate. A CLI and a web chat (Python's built-in `http.server`, on 127.0.0.1 only) share one login and context core. The context is assembled from the active mandate's files only.
- **Key files:** `services/chat/session.py`, `services/chat/cli.py`, `services/chat/web.py`
- **Try it:** `PYTHONPATH=src python -m services.chat.cli --mandate mandate_a`, or `PYTHONPATH=src python -m services.chat.web` and open http://127.0.0.1:8765
- **Result:** 7 tests in `test_part0.py`. A bad token, an unknown user or an unserved mandate is refused. Mandate A's context holds no mandate B names, codes, canaries or file lines, and the same holds the other way round. Self-check: workspace files present, and SOUL.md names the agent.

## Notebook 24: MCP tool server (Part 1)

- **Built:** an MCP SDK v2 server with exactly the closed catalog of 12 tools. Inputs and outputs are Pydantic schemas, errors are typed (`SCOPE_DENIED`, `HOST_DENIED`, `NOT_FOUND`, `POLICY_BLOCKED`), and scope is checked on every call. A stdio entry reads the token only from the environment.
- **Key files:** `services/mcp/server.py`, `services/mcp/schemas.py`, `services/mcp/errors.py`
- **Try it:** `CREDITWATCH_MANDATE=mandate_a PYTHONPATH=src python -m services.mcp.server`, which waits for an MCP client over stdio.
- **Result:** 13 tests in `test_part1_mcp.py`. There's a contract test per tool, the server registers exactly the catalog, and no stub is left. Self-check: REGISTRY matches the closed catalog.

## Notebook 25: the real model behind ReplayClient (Part 2)

- **Built:** OpenAI over `httpx`, behind the kit's `ReplayClient`.
  - It's recorded once, then replayed in tests and CI from the committed cache.
  - Triage, plus heartbeat-kind (materiality) tasks, go to gpt-5.4-nano; the agent route is gpt-5.4-mini.
  - The API key is read only from `OPENAI_API_KEY` at call time and is never stored, logged or cached.
  - Record sessions stop at a $1.00 cap.
  - The default provider is the stand-in, so plain commands reproduce the graded outputs.
- **Key files:** `services/llm/model.py`, `services/llm/openai_provider.py`, `services/llm/routes.py`, `services/llm/estimate.py`, `evals/replay/`
- **Try it (no API call):** `./lantern eval twice --task dev-01 --provider openai`, and `./lantern eval run --provider openai --suite dev`
- **Result:** 16 tests in `test_part2_model.py`, covering:
  - replay is exact, and an unrecorded request is a cache miss;
  - the key appears in no body, error message or cache file;
  - routing, and the budget guard stopping cleanly;
  - dev-01 replayed twice with identical traces (`ebc5a766...`).

  The real model passes 5/8. The failures, cost and safety notes are in [reports/real_model_run.md](reports/real_model_run.md).

## Notebook 26: skills (Part 3)

- **Built:** six skills, with `signal-explainer` gaining a second deterministic script (`explain_signal.py`). The skills are client-neutral. The lockfile is regenerated, and the chat refuses to start on a tampered or unlocked skill. `skills_index` is raised to 320 tokens in `params.yaml`.
- **Key files:** `agent/skills/*/SKILL.md`, `agent/skills/signal-explainer/scripts/explain_signal.py`, `agent/skills/skills.lock`
- **Try it:** `PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests/test_part3_skills.py -q -s`, which prints the token saving.
- **Result:** 16 tests in `test_part3_skills.py`. The index is 250 tokens against 1,015 for every SKILL.md, a **75% saving**. A one-line edit, an edited script or an unlocked skill is refused. Self-check: six skills with two scripts, and skills.lock verifies.

## Notebook 27: governed memory (Part 4)

- **Built:**
  - `memory_read`: one FTS5 store per mandate, rebuilt from that mandate's own log.
  - `memory_propose_write`: only Priya's authenticated, unedited message becomes a rule; everything else becomes a proposal.
  - The ledger keeps ids and hashes only.
  - A compaction job dry-runs a review diff, and writes only with `--apply`.
  - `MEMORY.md` is loaded into the context.
- **Key files:** `services/memory/store.py`, `services/memory/compact.py`, `services/mcp/server.py`
- **Try it (dry run):** `PYTHONPATH=src python -m services.memory.compact --mandate mandate_a`
- **Result:** 11 tests in `test_part4_memory.py`. These are canary tests over memory reads, the compaction output and the assembled context, in both directions. Cross-session recall works for the same client only, and a forged message becomes a proposal.

## Notebook 28: heartbeat (Part 5)

- **Built:**
  - Our pairing: exact first, then normalised (rates, spreads and dates removed), one-to-one, with ambiguous matches left unpaired.
  - The four jobs and their thin DAGs: release watch, EDGAR watch, monthly brief and hygiene.
  - Alerts, decisions, briefs and metrics outputs.
  - A simulated day, and a hand-check sampler for the real panel.
- **Key files:** `services/signals/pairing.py`, `services/signals/sample.py`, `services/heartbeat/jobs.py`, `services/heartbeat/run.py`, `services/heartbeat/metrics.py`, `dags/*.py`
- **Try it (dry run):** `PYTHONPATH=src python -m services.heartbeat.run`
- **Result:** 24 tests in `test_part5_heartbeat.py`.
  - Precision and recall are 1.00 / 1.00 on the fixture and variants 1 to 3; the baseline recall was 0.67.
  - Routing counts are taken from the ground truth: 8 signals for mandate_a, 4 for mandate_b.
  - Release to brief takes 90 minutes, and the idle cost is $0.00.

  Self-check: pairing, alerts.jsonl fields, and alerts routed only to holders. The real panel has 40,258 rows, and its hand-check sample is in `outputs/pairing/hand_check.csv`.

## Notebook 29: actions and approvals (Part 6)

- **Built:**
  - A policy engine that reads each action class's policy from `params.yaml`.
  - `draft_email`, which runs the standing rules on the draft itself.
  - `request_approval`, and Priya's `grant()` (not a tool), which issues an HMAC token for the exact payload.
  - `send_email`, which goes to the mock outbox only, once per draft.
  - `watchlist_write`, which writes `- BDC: reason (added YYYY-MM-DD)` and reads back.
- **Key files:** `services/actions/engine.py`, `services/actions/approve.py`, `services/actions/store.py`, `services/mcp/server.py`
- **Try it:** `PYTHONPATH=src python -m pytest -p no:cacheprovider services/tests/test_part6_actions.py -q`
- **Result:** 34 tests in `test_part6_actions.py`.
  - **At draft time:** `R-OTHER-MANDATE` (including hidden codes and canaries), `R-RESTRICTED`, `R-BORROWER` and `R-NOT-CONTACT`.
  - **At send time:** no token, malformed, `payload_changed`, expired, wrong approver, and sent twice.
  - **On the watchlist:** no token gives "requested", an edit after approval is blocked, and another client's names are blocked.
  - An approved send reaches the mock outbox.

## Notebook 30: learning loop (Part 7)

- **Built:** the four corrections are replayed through the channel into our agent, with the client note run before and after each one. The ledger is saved to `logs/audit/`. The replay runs once only.
- **Key files:** `services/learning/replay.py`, `services/learning/agent.py`, `services/learning/checks.py`
- **Try it:** `PYTHONPATH=src python -m services.learning.replay`. It's already applied, so it now replies "Refused: already replayed".
- **Result:** 16 tests in `test_part7_learning.py`. Every correction goes from False before to True after, with earlier rules held and no regressions. A pre-written rule fails provenance, and a forged correction becomes a proposal. Self-check: learned rules trace to `memory_write` events, and before-and-after runs exist for every correction.

## Notebook 31: task suite, release morning, economics (Part 8)

- **Built:**
  - `./lantern eval run --suite dev|variant --mode replay`, with the Appendix G checks. Each task runs in its own sandbox.
  - The release-morning scenario, which includes a capstone: one word swapped after approval is blocked.
  - The unit-economics table, including cost per mandate-month. The $250,000 leak cost is labelled as an assumption.
- **Key files:** `lantern`, `services/evals/`, `services/scenario/release_morning.py`, `services/economics/report.py`
- **Try it (dry runs):**
  - `./lantern eval run --suite dev --mode replay`
  - `PYTHONPATH=src python -m services.scenario.release_morning`
  - `PYTHONPATH=src python -m services.economics.report`
- **Result:** 23 tests in `test_part8_suite.py`.
  - The dev suite passes **8/8** with the stand-in.
  - The scenario records `mock-0001`, a watchlist entry id and both approval hashes, and finds no other-client hits in anything sent.
  - Cost per mandate-month is $0.0205 for mandate_a and $0.0154 for mandate_b.
  - The variant suite reports "not released".

  Self-check: dev.jsonl fields, dev tasks pass, the scenario record, and no other mandate in any alert or task output.

## Still open

- The hand-check of `outputs/pairing/hand_check.csv`, which is a human judgement.
- Whether to commit `data/real/panel_2p.parquet` (1.7 MB).
