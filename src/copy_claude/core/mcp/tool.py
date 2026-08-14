from __future__ import annotations

from typing import Any

from copy_claude.core.mcp.client import McpClient, McpToolDef, McpServerUnavailableError, McpToolError
from copy_claude.core.tools.base import BaseTool, ToolResult


# 将 MCP 工具包装为 BaseTool，使 ToolRegistry 可透明调用
class McpTool(BaseTool):
    params_model = None  # input_schema 来自 MCP tool_def，不使用 pydantic model

    # 初始化 MCP 工具包装器，工具名以 server_name__ 为前缀防止命名冲突
    def __init__(self, client: McpClient, server_name: str, tool_def: McpToolDef) -> None:
        self._client = client # 只有mcp_client有能力通过mcpserver调用mcp工具
        self._server_name = server_name # servername前缀用来与本地工具区分
        self._tool_def = tool_def # 包含name,description和input_schema，工具三件套
        self.name = f"{server_name}__{tool_def.name}"
        self.description = tool_def.description or f"MCP tool from {server_name}"
        self.input_schema: dict[str, Any] = (
                tool_def.input_schema or {"type": "object", "properties": {}}
        )
# 调用 MCP server 上的工具，连接不可用或工具执行失败时返回 is_error=True
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        try:
            content = await self._client.call_tool(self._tool_def.name, dict(params)) # 输入参数和工具名调用mcp工具
            return ToolResult(content=content)
        except McpServerUnavailableError as exc:
            return ToolResult(
                content=f"mcp server '{self._server_name}' unavailable: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        except McpToolError as exc:
            return ToolResult(
                content=f"mcp tool '{self.name}' error: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        except Exception as exc:
            return ToolResult(
                content=f"mcp tool '{self.name}' unexpected error: {exc}",
                is_error=True,
                error_type="runtime_error",
            )