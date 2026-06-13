from __future__ import annotations

import asyncio
import time
from copy_claude.core.llm.types import ToolCallBlock
from copy_claude.core.tools.base import BaseTool,ToolResult
from copy_claude.core.tools.registry import ToolRegistry
from copy_claude.core.events.bus import EventBus
from copy_claude.core.bus.events import (
        ToolCallStartedEvent,
        ToolCallFinishedEvent,
        ToolCallFailedEvent
)
from datetime import datetime,UTC
from typing import cast

_DEFAULT_TIMEOUT: float = 120.0 # 默认超时阈值
def _now() -> str:
    return datetime.now(UTC).isoformat()

# 发布 ToolCallFailedEvent 并返回对应 ToolResult

async def _fail( # 报错，谁报错，什么时候报错
        bus:EventBus, # 用于广播，必传入
        run_id: str, # 对话标识符，必传入
        tool_call :ToolCallBlock, # 调用失败的工具请求，必传入
        error_class : str, # 报错类型，必传入
        error_message:str, # 报错信息，必传入
        elapsed_ms: int,
        *,
        attempt: int = 1,
)->ToolResult:
    await bus.publish(ToolCallFailedEvent(
            run_id=run_id,
            tool_use_id = tool_call.id,
            tool_name = tool_call.name,
            error_class = error_class,
            error_message = error_message,
            elapsed_ms = elapsed_ms,
            attempt = attempt,
            ts=_now()
    ))
    return ToolResult(content=error_message,is_error=True,error_type=error_class)




# 校验参数、检查权限（看在不在工具箱里）、限时调用工具、发布进度事件，失败时指数退避重试，返回 ToolResult（不抛异常）
async def invoke_tool(
        registry: ToolRegistry, # 工具箱，用来判断有没有调用的工具，以及工具输入参数对不对。
        tool_call: ToolCallBlock, # llm需要的工具本身。
        bus:EventBus, # 事件广播器，播报调用情况。
        run_id:str, # 对话标识符
        timeout:float=_DEFAULT_TIMEOUT,
        # *,
        # permission_manager: PermissionManager | None = None,
        # session_id: str = "",
)->ToolResult:
    t0 = time.monotonic()
    def elapsed()->int:
        return int((time.monotonic()-t0)*1000)
    await bus.publish(ToolCallStartedEvent(run_id=run_id,
                                           tool_use_id = tool_call.id,
                                           tool_name = tool_call.name,
                                           params = tool_call.input,
                                           ts = _now()
                                           )) # 广播工具开始调用事件。

    # 1先确定工具是否存在
    tool = registry.get(tool_call.name)
    if tool is None:
        return await _fail(
            bus=bus,
            run_id=run_id,
            tool_call=tool_call,
            error_class="runtime_error",
            error_message=f"unknown tool: {tool_call.name}",
            elapsed_ms=elapsed())

    # 2判断工具参数是否对应
    required = cast(list[str], tool.input_schema.get("required", [])) # 强转dict的键为列表
    missing = [p for p in required if p not in tool_call.input] # 必要参数但输入没给
    if missing:
        return await _fail(
            bus=bus,
            run_id=run_id,
            tool_call=tool_call,
            error_class="schema_error",
            error_message=f"missing required parameters: {', '.join(missing)}",
            elapsed_ms=elapsed())
    # 3工具存在，参数正确，开始尝试调用，超时返回失败
    try:
        result = await asyncio.wait_for(tool.invoke(dict(tool_call.input)),timeout=timeout)
        await bus.publish(ToolCallFinishedEvent(run_id=run_id,
                                                tool_use_id = tool_call.id,
                                                tool_name = tool_call.name,
                                                elapsed_ms=elapsed(),
                                                ts=_now()))
        return result # 成功调用结果
    except asyncio.TimeoutError: # 超时报错
        return await _fail(
            bus=bus,
            run_id=run_id,
            tool_call=tool_call,
            error_class="timeout",
            error_message=f"tool timed out after {timeout}s",
            elapsed_ms=elapsed())

    except Exception as exc: # 未知错误。
        return await _fail(bus=bus,
                           run_id=run_id,
                           tool_call=tool_call,
                           error_class= "runtime_error",
                           error_message= str(exc),
                           elapsed_ms=elapsed())