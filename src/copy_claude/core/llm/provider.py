import os
import anthropic
from typing import Any
from copy_claude.core.llm.types import LlmResponse, ToolCallBlock, UsageStatus
from copy_claude.core.bus.events import LlmModelSelectedEvent, LlmTokenEvent, LlmUsageEvent
from datetime import datetime, UTC
import httpx
import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-opus-4-7": 200_000,
}


def _context_window(model: str) -> int:
    return _MODEL_CONTEXT_WINDOWS.get(model, 200_000)


SYSTEM_PROMPT = (  # 系统基本人设
    "You are a helpful AI assistant.Your name is Ama01 or 凯尔希,You can call user as Doctor or 博士."
    "Use the available tools to complete the user's goal. "
    "When the goal is fully achieved, respond with a final answer and do not call any more tools."
)

_MAX_STREAM_RETRIES = 3  # S6获取字符重试次数
_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)  # S6失败后的等待时间


class AnthropicProvider:
    def __init__(self, model: str, client: Any = None, persona: Path | None = None) -> None:
        if client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")  # 这是env中保留的api_key，在这里与llm产生联系。
            if not api_key:
                raise SystemExit("ANTHROPIC_API_KEY not set")
            self._client: Any = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            self._client = client
        self._model = model
        self._prompt = SYSTEM_PROMPT
        if persona:
            ps = persona.read_text(encoding="utf-8")
            self._prompt += "严格遵循以下人设，全程不跳出角色：\n\n"
            self._prompt += ps

    async def chat(self, messages, tool_schemas, bus, run_id, step: int = 0, system: Any | None = None) -> LlmResponse:
        # 告诉监听者用了哪个模型
        await bus.publish(LlmModelSelectedEvent(run_id=run_id, model=self._model, strategy="static", ts=_now()))

        # system prompt：告诉 LLM 它是谁、能做什么
        system_blocks: list[dict[str, object]] = [
            {"type": "text",
             "text": self._prompt,
             "cache_control": {"type": "ephemeral"}}
        ]

        tools: list[dict[str, object]] = list(tool_schemas)
        if tools:
            last = dict(tools[-1])
            last["cache_control"] = {"type": "ephemeral"}
            tools = tools[:-1] + [last]

        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": 8192,
            "system": system_blocks,
            "messages": messages,
        }

        if tools:
            kwargs["tools"] = tools
        text_parts: list[str] = []
        final_message: Any | None = None
        # 流式调用S6:增加重试次数。
        for attempt in range(1, _MAX_STREAM_RETRIES + 1):
            try:
                text_parts = []
                async with self._client.messages.stream(**kwargs) as stream:
                    # **kwargs 是一种用于函数调用的语法，表示将一个字典解包为关键字参数传递给函数。
                    # **kwargs 将上面构造的字典解包为命名参数传递给 stream() 方法，相当于显式写出
                    # stream(model=..., max_tokens=..., system=..., messages=..., tools=...)。
                    # 这样做的目的是让参数构造与调用解耦，便于动态添加或修改参数。
                    async for text in stream.text_stream:
                        if attempt == 1:  # S6这里只在attempt==1时打印是为了防止网络断流重新复制一份
                            await bus.publish(LlmTokenEvent(run_id=run_id, token=text, ts=_now()))
                        text_parts.append(text)
                    final_message = await stream.get_final_message()
                break
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as exc:
                # S6报错就要重试。
                if attempt == _MAX_STREAM_RETRIES:
                    log.error(
                        "stream failed after %d attempts run_id=%s step=%d: %s",
                        _MAX_STREAM_RETRIES, run_id, step, exc,
                    )
                    raise
                delay = _RETRY_BACKOFF_S[attempt - 1]
                log.warning(
                    "stream dropped (attempt %d/%d) run_id=%s step=%d: %s — retrying in %.0fs",
                    attempt, _MAX_STREAM_RETRIES, run_id, step, exc, delay,
                )
                await asyncio.sleep(delay)

                # 通过 get_final_message() 获取完整的最终消息对象（包含 stop_reason、content 等完整信息）。
        assert final_message is not None
        tool_calls = []
        usage = final_message.usage  # S6流信息能够得到大模型的窗口使用情况
        cache_read: int = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create: int = getattr(usage, "cache_creation_input_tokens", 0) or 0
        context_pct = usage.input_tokens / _context_window(self._model)  # 得到上下文占据多大窗口了。
        # S6将token使用情况信息作为事件广播给各个订阅者。
        await bus.publish(
            LlmUsageEvent(
                run_id=run_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                context_pct=context_pct,
                ts=_now(),
            )
        )
        for block in final_message.content:
            if block.type == "tool_use":
                tool_calls.append(
                    ToolCallBlock(id=block.id, name=block.name, input=dict(block.input))
                )

        return LlmResponse(
            stop_reason=final_message.stop_reason or "end_turn",
            tool_calls=tool_calls,
            text="".join(text_parts),
            usage=UsageStatus(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                context_pct=context_pct,
            )
        )
