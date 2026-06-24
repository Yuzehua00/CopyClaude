import os
import anthropic
from typing import Any
from copy_claude.core.llm.types import LlmResponse, ToolCallBlock
from copy_claude.core.bus.events import LlmModelSelectedEvent, LlmTokenEvent
from datetime import datetime, UTC


def _now() -> str:
    return datetime.now(UTC).isoformat()


_SYSTEM_PROMPT = (  # 系统基本人设
    "You are a helpful AI assistant.Your name is Ama01 or 凯尔希,You can call user as Doctor or 博士."
    "Use the available tools to complete the user's goal. "
    "When the goal is fully achieved, respond with a final answer and do not call any more tools."
)


class AnthropicProvider:
    def __init__(self, model: str, client: Any = None) -> None:
        if client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")  # 这是env中保留的api_key，在这里与llm产生联系。
            if not api_key:
                raise SystemExit("ANTHROPIC_API_KEY not set")
            self._client: Any = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            self._client = client
        self._model = model

    async def chat(self, messages, tool_schemas, bus, run_id, step: int = 0, system: Any | None = None) -> LlmResponse:
        # 告诉监听者用了哪个模型
        await bus.publish(LlmModelSelectedEvent(run_id=run_id, model=self._model, strategy="static", ts=_now()))

        # system prompt：告诉 LLM 它是谁、能做什么
        system_blocks: list[dict[str, object]] = [
            {"type": "text",
             "text": _SYSTEM_PROMPT,
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
        # 流式调用
        async with self._client.messages.stream(**kwargs) as stream:
            # **kwargs 是一种用于函数调用的语法，表示将一个字典解包为关键字参数传递给函数。
            # **kwargs 将上面构造的字典解包为命名参数传递给 stream() 方法，相当于显式写出
            # stream(model=..., max_tokens=..., system=..., messages=..., tools=...)。
            # 这样做的目的是让参数构造与调用解耦，便于动态添加或修改参数。
            async for text in stream.text_stream:
                await bus.publish(LlmTokenEvent(run_id=run_id, token=text, ts=_now()))
                text_parts.append(text)
            final_message = await stream.get_final_message()
            # 通过 get_final_message() 获取完整的最终消息对象（包含 stop_reason、content 等完整信息）。

        tool_calls = []
        for block in final_message.content:
            if block.type == "tool_use":
                tool_calls.append(
                    ToolCallBlock(id=block.id, name=block.name, input=dict(block.input))
                )

        return LlmResponse(
            stop_reason=final_message.stop_reason or "end_turn",
            tool_calls=tool_calls,
            text="".join(text_parts),
        )
