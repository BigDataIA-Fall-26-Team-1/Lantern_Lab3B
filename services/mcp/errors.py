"""Typed tool errors. Each carries a code that the client sees in the MCP error
result ("SCOPE_DENIED: ..."), so a refusal is an observation, never a crash.

They subclass the SDK's ToolError, which marks an EXPECTED failure: the SDK
returns it as CallToolResult(is_error=True) instead of logging a traceback.
"""
from mcp.server.mcpserver.exceptions import ToolError as _SDKToolError


class ToolError(_SDKToolError):
    code = "TOOL_ERROR"
    rule_id = None

    def __init__(self, detail):
        self.detail = detail
        super().__init__(f"{self.code}: {detail}")


class ScopeDenied(ToolError):
    """Scope outside the session: another mandate, or public on a mandate tool."""
    code, rule_id = "SCOPE_DENIED", "R-SCOPE"


class HostDenied(ToolError):
    """A URL outside the SEC hosts in params.yaml."""
    code, rule_id = "HOST_DENIED", "R-HOST"


class NotFound(ToolError):
    """No such filing or BDC in the panel."""
    code = "NOT_FOUND"
