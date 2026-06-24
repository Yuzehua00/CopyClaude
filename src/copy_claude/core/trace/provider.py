from __future__ import annotations

from copy_claude.core.llm.base import LLMProvider,LlmResponse
from copy_claude.core.trace.writer import TraceWriter
from copy_claude.core.trace.record import TraceRecord
from copy_claude.core.events.bus import EventBus
from datetime import datetime,UTC
import time
import dataclasses
def _now():
    return datetime.now(UTC).isoformat()
class TraceProvider:
    def __init__(self, trace:TraceWriter,inner: LLMProvider,*,include_payload:bool=True):
        self._trace = trace
        self._inner = inner
        self._include_payload = include_payload

    async def chat(self,
                   messages: list[dict[str, any]],  # 上下文，必传
                   tool_schemas: list[dict[str, object]],  # 工具说明，必传
                   bus: EventBus,  # 事件订阅器，用于记录信息，必传。
                   run_id: str,  # 对话标识符，必传
                   *,
                   step: int = 0,  # 当前步数
                   system: str | None = None,  # 未知
                   ) -> LlmResponse:
        # 调用前记录core->llm发信息，调用后记录llm->core发信息。
        if self._include_payload:
            chat_data = {"messages": messages,"tool_schemas":tool_schemas}
        else:
            chat_data = {"messages":len(messages),"tool_schemas":len(tool_schemas)}
        # 调用前信息
        self._trace.emit(TraceRecord(
            ts=_now(),
            direction="CORE->LLM",
            layer="llm",
            kind="api_call",
            run_id=run_id,
            step=step,
            data=chat_data,
        ))
        t0 = time.monotonic()
        result = await self._inner.chat(messages, tool_schemas, bus, run_id, step=step)
        latency_ms = int((time.monotonic() - t0) * 1000)
        resp_data:dict[str,any]
        if self._include_payload:
            resp_data={
                "stop_reason":result.stop_reason,
                "tool_calls":[dataclasses.asdict(tc) for tc in result.tool_calls],
                "text":result.text,
                "usage":dataclasses.asdict(result.usage) if result.usage else None,
                "latency_ms": latency_ms,
            }
        else:
            resp_data={
                "stop_reason": result.stop_reason,
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }
        self._trace.emit(TraceRecord(
            ts=_now(),
            direction="LLM->CORE",
            layer="llm",
            kind="api_response",
            run_id=run_id,
            step=step,
            data=resp_data,
        ))
        return result