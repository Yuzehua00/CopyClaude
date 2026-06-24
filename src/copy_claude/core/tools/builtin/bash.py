from __future__ import annotations

import asyncio

from copy_claude.core.tools.base import ToolResult, BaseTool

_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB
_DEFAULT_TIMEOUT = 60


class BashTool(BaseTool): # 把大模型生成的shell指令交给subprocess_shell执行。
    name = "bash"
    description = (
        "Execute a shell command and return its output (stdout + stderr combined). "
        "Non-interactive only — commands requiring user input will hang and time out. "
        "Prefer short, focused commands. Output is truncated at 64 KB."
    )
    # 执行 Shell 命令并返回其输出（标准输出与标准错误合并）。
    # 仅限非交互式命令——需要用户输入的命令会挂起并超时。
    # 请优先使用简短、目的明确的命令。输出会被截断至 64 KB。

    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Maximum seconds to wait (default {_DEFAULT_TIMEOUT}, max 120).",
            },
        },
        "required": ["command"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:  # params对应properties
        command = str(params["command"])  # command字段一定有，但timeout字段不一定有
        timeout = min(int(str(params.get("timeout", _DEFAULT_TIMEOUT))), 120)
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd=command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout_bytes,_ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult(content=f"[timeout after {timeout}s]",is_error=True,error_type="timeout")
        except Exception as exc:
            return ToolResult(content=f"[command failed:{exc}]",is_error=True,error_type="runtime_error")

        output = stdout_bytes.decode("utf-8",errors="replace") # 字节解码成中文

        truncated = len(stdout_bytes) > _MAX_OUTPUT_BYTES # 截断应该对比字节而非解码后的内容
        if truncated:
            output = output[:_MAX_OUTPUT_BYTES] + "\n[truncated]"

        return_code = proc.returncode or 0
        if return_code != 0:
            return ToolResult(
                content=f"[exit {return_code}]\n{output}]",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=output or "[no output]")

