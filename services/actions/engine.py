"""Part 6: the policy engine. Every tool call passes through gate(), which reads
the tool's policy from params.yaml (actions.policy by action class, then
actions.overrides by tool): least agency, as a table (tutorial slide 33).

    auto       run
    notify     run, and record a notify event
    approve    run only with Priya's token for the exact payload (checked by the tool)
    principal  only Priya grants it; the agent may only request
    forbidden  refuse
"""
from services.mcp.errors import PolicyBlocked

POLICIES = {"auto", "notify", "approve", "principal", "forbidden"}
TOKEN_TOOLS = {"send_email", "watchlist_write"}      # tools that carry and verify an approval token
REQUEST_TOOLS = {"request_approval"}                 # tools that ask the principal; they never grant


class Engine:
    def __init__(self, P, registry):
        a = P["actions"]
        self.policy, self.overrides, self.registry = a["policy"], a.get("overrides") or {}, registry
        bad = sorted({c for c in registry.values() if c not in self.policy}
                     | {v for v in [*self.policy.values(), *self.overrides.values()] if v not in POLICIES}
                     | {t for t in self.overrides if t not in registry})
        if bad:
            raise ValueError(f"params.yaml actions: unknown class, policy or tool {bad}")

    def policy_for(self, tool):
        return self.overrides.get(tool, self.policy[self.registry[tool]])

    def gate(self, tool, audit, scope=None):
        """Returns the tool's policy, or raises PolicyBlocked (logged as policy_block)."""
        p = self.policy_for(tool)

        def block(rule, why):
            audit.record("policy_block", tool=tool, rule_id=rule, scope=scope, decision="blocked")
            raise PolicyBlocked(rule, why)
        if p == "forbidden":
            block("R-FORBIDDEN", f"{tool} is forbidden by params.yaml")
        if p == "approve" and tool not in TOKEN_TOOLS:
            block("R-APPROVAL", f"{tool} needs approval but carries no approval token")
        if p == "principal" and tool not in REQUEST_TOOLS:
            block("R-PRINCIPAL", f"{tool} is the principal's action; the agent may only request it")
        if p == "notify":
            audit.record("notify", tool=tool, scope=scope)
        return p
