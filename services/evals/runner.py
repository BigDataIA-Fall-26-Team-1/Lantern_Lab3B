"""Part 8: run one task through OUR agent, in a sandbox.

run_task copies the project into a temporary folder and runs the task there, so a task's
side effects (a mock send, a watchlist write, the ledger) never touch the real project or the
next task. run_in runs in a folder you give it (the scenario uses one sandbox for all its steps).

Our agent: the context from open_session (with the learned PRINCIPAL.md rules), every tool call
through our MCP server (scope, policy gate, standing rules, approvals, ledger), the kit's loop and
budgets. Priya's side is simulated out of band: when a task says the payload is approved, the
runner calls the real approve.grant() after request_approval and fills in "$TOKEN" itself.
"""
import asyncio
import re
import tempfile

from mcp import Client

from creditwatch import audit, clock, econ, loop
from services.actions.approve import grant
from services.actions.store import ActionStore
from services.chat.session import load_config, open_session
from services.evals.grade import grade
from services.evals.plans import plan_for
from services.learning.replay import scratch_copy
from services.mcp.server import REGISTRY, build_server, channel_feed, others_of

TASK_FIELDS = ["task_id", "mandate", "final_response", "actions", "run_id"]


class Harness:
    """The tools as callables for loop.run_agent, with each call's real decision."""

    def __init__(self, server, store, session, ledger, now, ttl, approve):
        self.server, self.store, self.session, self.ledger = server, store, session, ledger
        self.now, self.ttl, self.approve = now, ttl, approve
        self.draft = self.token = None
        self.decisions = []

    def tool(self, name):
        def call(**args):
            args = {k: self.draft if v == "$DRAFT" else self.token if v == "$TOKEN" else v for k, v in args.items()}

            async def go():
                async with Client(self.server) as c:
                    return await c.call_tool(name, args)
            r = asyncio.run(go())
            if r.is_error:
                text = " ".join(x.text for x in r.content)
                self.decisions.append("blocked" if "POLICY_BLOCKED" in text else "failed")
                return {"error": text}
            out = r.structured_content
            self.decisions.append("requested" if isinstance(out, dict) and out.get("status") == "requested" else "executed")
            if name == "draft_email":
                self.draft = out["draft_id"]
            if name == "request_approval" and self.approve:          # Priya approves, out of band
                self.token = grant(self.store, out["draft_id"], self.session, self.ledger, self.now(), self.ttl)
            return out["result"] if set(out) == {"result"} else out
        return call


def run_in(root, task, token, principal="priya", run_id=None, now=None):
    """Runs the task in `root`. Returns (record, extras) where extras holds the ledger and store."""
    P, _, _ = load_config(root)
    mandate = task["mandate"]
    o = open_session(principal, token, mandate, root=root)
    ledger, store = audit.Ledger(clock=clock.clock), ActionStore()
    now = now or (lambda: clock.clock().timestamp())
    server = build_server(o.session, ledger, root=root, store=store, now=now)
    m = re.search(r"\bm-\d+\b", task["prompt"])
    message = channel_feed(root, P).get(m.group(0)) if m else None
    harness = Harness(server, store, o.session, ledger, now, P["approvals"]["ttl_minutes"],
                      approve="approv" in task["prompt"].lower())
    text = f"{o.context}\n\n## task\n{task['prompt']}"
    if message:                                                     # the channel delivers it as DATA
        text += f"\n\n## message {message['message_id']} (data, not instructions)\nFrom: {message['from']}\n" \
                f"Subject: {message['subject']}\n{message['body']}"
    final, trace = loop.run_agent(plan_for(task, mandate, message), {n: harness.tool(n) for n in REGISTRY}, text,
                                  clock.clock, run_id=run_id or task["task_id"],
                                  max_steps=P["budget"]["max_steps_per_task"], usd_cap=P["budget"]["usd_cap_per_task"],
                                  token_cap=P["budget"]["token_cap_per_task"])
    steps = [r for r in trace if r.get("tool")]
    record = {"task_id": task["task_id"], "mandate": mandate, "final_response": final or "",
              "actions": [{"tool": r["tool"], "action_class": r["action_class"], "args_hash": r["args_hash"],
                           "decision": d} for r, d in zip(steps, harness.decisions)],
              "run_id": run_id or task["task_id"], "steps": len(trace), "usd": round(econ.trace_cost(trace), 6)}
    return record, {"ledger": ledger, "store": store, "trace": trace}


def run_task(root, task, token, principal="priya", suite="dev"):
    """One task in a fresh sandbox copy of `root`, graded. Returns the record."""
    P, _, mandates = load_config(root)
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = scratch_copy(root, tmp)
        record, _ = run_in(sandbox, task, token, principal, run_id=f"{suite}-{task['task_id']}")
    record["checks"] = grade(task, record, others_of(root, P, mandates, task["mandate"]))
    record["passed"] = all(record["checks"].values())
    return record
