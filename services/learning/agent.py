"""Part 7: run one task through OUR agent.

Our agent is the harness built so far: the context from open_session (SOUL,
PRINCIPAL.md with its learned rules, AGENTS, the mandate's files, the skills
index, memory), the tools of our MCP server (scope, policy gate and ledger on
every call), and the kit's loop.run_agent with its budgets. The model is still
the kit's deterministic stand-in: a real model arrives in Part 2, behind the
same harness. The stand-in follows a rule only when its text is in the context.
"""
import asyncio

from mcp import Client

from creditwatch import clock, loop, standin
from services.chat.session import load_config, open_session
from services.mcp.server import build_server


def plan_for(mandate):
    return [{"tool": "mandate_profile", "args": {"scope": mandate}, "rationale": "the client's positions"},
            {"tool": "bdc_signals", "args": {"scope": "public"}, "rationale": "what changed this release"},
            {"tool": "bdc_holdings", "args": {"scope": "public"}, "rationale": "terms behind each signal"}]


def mcp_tools(server, decisions):
    """The server's tools as plain callables for loop.run_agent. A refusal comes back
    as an observation, never a crash; each call's decision is kept for the action list."""
    def tool(name):
        def call(**args):
            async def go():
                async with Client(server) as c:
                    return await c.call_tool(name, args)
            r = asyncio.run(go())
            decisions.append("failed" if r.is_error else "executed")
            if r.is_error:
                return {"error": " ".join(x.text for x in r.content)}
            out = r.structured_content
            return out["result"] if set(out) == {"result"} else out
        return call
    return {name: tool(name) for name in ("list_mandates", "bdc_holdings", "bdc_signals", "get_filing",
                                          "mandate_profile", "watchlist_read", "memory_read")}


def run_task(root, principal, token, mandate, prompt, audit, run_id="run-1"):
    """Returns {final_response, trace, actions, report}. Actions carry the fields the
    task suite grades: tool, action_class, args_hash, decision."""
    P, _, _ = load_config(root)
    o = open_session(principal, token, mandate, root=root)        # refuses on tampered skills or bad token
    decisions = []
    tools = mcp_tools(build_server(o.session, audit, root=root), decisions)
    model = standin.FixtureModel(plan_for(mandate), standin.note_writer)
    task = f"{o.context}\n\n## task\n{prompt}"                     # the assembled context, then the task
    text, trace = loop.run_agent(model, tools, task, clock.clock, run_id=run_id,
                                 max_steps=P["budget"]["max_steps_per_task"],
                                 usd_cap=P["budget"]["usd_cap_per_task"],
                                 token_cap=P["budget"]["token_cap_per_task"])
    steps = [r for r in trace if r.get("tool")]
    actions = [{"tool": r["tool"], "action_class": r["action_class"], "args_hash": r["args_hash"], "decision": d}
               for r, d in zip(steps, decisions)]
    return {"final_response": text, "trace": trace, "actions": actions, "report": o.report}
