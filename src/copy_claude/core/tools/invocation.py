from __future__ import annotations

import asyncio
import time

from pydantic_core._pydantic_core import ValidationError

from copy_claude.core.llm.types import ToolCallBlock
from copy_claude.core.tools.base import BaseTool, ToolResult
from copy_claude.core.tools.registry import ToolRegistry
from copy_claude.core.events.bus import EventBus
from copy_claude.core.bus.events import (
    ToolCallStartedEvent,
    ToolCallFinishedEvent,
    ToolCallFailedEvent,
    PermissionRequestedEvent, PermissionGrantedEvent, PermissionDeniedEvent
)
from copy_claude.core.permission.manager import PermissionManager
from datetime import datetime, UTC
from copy_claude.core.tools.errors import RateLimitedError
from typing import cast

_DEFAULT_TIMEOUT: float = 120.0
_MAX_RETRIES: int = 2
_RETRY_BASE_S: float = 2.0  # backoff base; tests can monkeypatch to 0
_RETRYABLE: frozenset[str] = frozenset({"runtime_error", "rate_limited"})

def _now() -> str:
    return datetime.now(UTC).isoformat()


# 发布 ToolCallFailedEvent 并返回对应 ToolResult

async def _fail(  # 报错，谁报错，什么时候报错
        bus: EventBus,  # 用于广播，必传入
        run_id: str,  # 对话标识符，必传入
        tool_call: ToolCallBlock,  # 调用失败的工具请求，必传入
        error_class: str,  # 报错类型，必传入
        error_message: str,  # 报错信息，必传入
        elapsed_ms: int,
        *,
        attempt: int = 1,
) -> ToolResult:
    await bus.publish(ToolCallFailedEvent(
        run_id=run_id,
        tool_use_id=tool_call.id,
        tool_name=tool_call.name,
        error_class=error_class,
        error_message=error_message,
        elapsed_ms=elapsed_ms,
        attempt=attempt,
        ts=_now()
    ))
    return ToolResult(content=error_message, is_error=True, error_type=error_class)


# 校验参数、检查权限（看在不在工具箱里）、限时调用工具、发布进度事件，失败时指数退避重试，返回 ToolResult（不抛异常）
async def invoke_tool(
        registry: ToolRegistry,  # 工具箱，用来判断有没有调用的工具，以及工具输入参数对不对。
        tool_call: ToolCallBlock,  # llm需要的工具本身。
        bus: EventBus,  # 事件广播器，播报调用情况。
        run_id: str,  # 对话标识符
        timeout: float = _DEFAULT_TIMEOUT,
        # *,
        permission_manager: PermissionManager | None = None,
        session_id: str = "",
) -> ToolResult:
    t0 = time.monotonic()
    def elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    await bus.publish(ToolCallStartedEvent(run_id=run_id,
                                           tool_use_id=tool_call.id,
                                           tool_name=tool_call.name,
                                           params=tool_call.input,
                                           ts=_now()
                                           ))  # 广播工具开始调用事件。

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

    # 2判断工具参数是否对应,先检查参数模型是否正确，再检查必要参数是否存在
    if tool.params_model is not None: # 检查参数是否完全对齐
        try:
            tool.params_model.model_validate(dict(tool_call.input))
        except ValidationError as exc:
            return await _fail(
                bus, run_id, tool_call,
                "schema_error", str(exc), elapsed(),
            )
    # required = cast(list[str], tool.input_schema.get("required", []))  # 强转dict的键为列表
    # missing = [p for p in required if p not in tool_call.input]  # 必要参数但输入没给
    # if missing:
    #     return await _fail(
    #         bus=bus,
    #         run_id=run_id,
    #         tool_call=tool_call,
    #         error_class="schema_error",
    #         error_message=f"missing required parameters: {', '.join(missing)}",
    #         elapsed_ms=elapsed())
    # 参数正确，确定调用工具能成功再进行权限检查。
    # 3工具存在，参数正确，开始尝试调用，超时返回失败
    if permission_manager is not None:
        async def _emit_permission(raw: dict[str, any]) -> None:
            await bus.publish(PermissionRequestedEvent(**raw, run_id=run_id))

        allowed, decision = await permission_manager.check_and_wait(
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            session_id=session_id,
            event_emitter=_emit_permission,
        )
        if allowed:
            if decision not in ("auto_allow",):
                await bus.publish(
                    PermissionGrantedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_now(),
                    )
                )
        else:
            if decision != "auto_deny":
                await bus.publish(
                    PermissionDeniedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_now(),
                    )
                )
            return await _fail(
                bus, run_id, tool_call,
                "permission_denied",
                "Permission denied by user. You may not execute this command. "
                "Try an alternative approach or ask the user what to do.",
                elapsed(),
            )
    for attempt in range(1, _MAX_RETRIES + 2):
        error_class: str | None = None
        error_message: str | None = None

        try:
            result = await asyncio.wait_for(
                tool.invoke(dict(tool_call.input)), timeout=timeout
            )
            ms = elapsed()

            if result.is_error:
                error_class = result.error_type or "runtime_error"
                error_message = result.content
            else:
                await bus.publish(
                    ToolCallFinishedEvent(
                        run_id=run_id,
                        tool_use_id=tool_call.id,
                        tool_name=tool_call.name,
                        elapsed_ms=ms,
                        output=result.content,
                        ts=_now(),
                    )
                )
                return result

        except RateLimitedError as exc:
            error_class = "rate_limited"
            error_message = str(exc)
        except TimeoutError:
            return await _fail(
                bus, run_id, tool_call,
                "timeout", f"tool timed out after {timeout}s", elapsed(),
                attempt=attempt,
            )
        except Exception as exc:
            error_class = "runtime_error"
            error_message = str(exc)

        assert error_class is not None and error_message is not None
        ms = elapsed()

        if error_class in _RETRYABLE and attempt <= _MAX_RETRIES:
            await bus.publish(
                ToolCallFailedEvent(
                    run_id=run_id,
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    error_class=error_class,
                    error_message=error_message,
                    elapsed_ms=ms,
                    attempt=attempt,
                    ts=_now(),
                )
            )
            await asyncio.sleep(_RETRY_BASE_S * (2 ** (attempt - 1)))
            continue

        return await _fail(
            bus, run_id, tool_call,
            error_class, error_message, ms,
            attempt=attempt,
        )

        # unreachable, but keeps mypy happy
    return ToolResult(content="internal error", is_error=True, error_type="runtime_error")
