from __future__ import annotations
# 用于调用大模型将完整的上下文压缩成摘要，保存重要信息。
import logging
from pathlib import Path
from typing import List, Any

from copy_claude.core.events.bus import EventBus
from copy_claude.core.llm.base import LLMProvider
from copy_claude.core.context import ExecutionContext
from copy_claude.core.bus.events import ContextCompactedEvent
logger = logging.getLogger(__name__)
from dataclasses import dataclass
from datetime import datetime,UTC
@dataclass
class CompactionResult: # 压缩结果。
    summary_text: str
    original_token_estimate: int
    summary_tokens: int
def _ts_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


# 返回当前 UTC 时间的 ISO 8601 字符串
def _now() -> str:
    return datetime.now(UTC).isoformat()

_COMPACT_PROMPT = """\
You are compressing an agent conversation into a handoff summary.
Another LLM instance will continue this task from your summary alone — make it complete.

Structure your response with exactly these six sections:

## 1. Original Goal
One sentence describing what the user asked the agent to accomplish.

## 2. Completed Steps
Bullet list of what has been done. Be specific (file paths, commands run, decisions made).

## 3. Key Constraints & Discoveries
Facts learned during the run that affect future decisions \
(e.g., API limitations, file formats, user preferences stated mid-conversation).

## 4. Current File State
For each file that was created or modified: path, a one-line description of its current state.

## 5. Remaining TODOs
Ordered list of what still needs to be done to complete the original goal.

## 6. Critical Data
Any values the next LLM needs verbatim: IDs, tokens, exact error messages, config values \
discovered during the run.

Be concise. Omit reasoning steps and intermediate attempts. Keep conclusions.\
"""
class Compactor:
    def __init__(self,
                 bus:EventBus, # 事件总线，用于广播事件
                 session_dir:Path, # 会话文件夹，用于获取完整session的路径
                 session_id:str
                 ):
        self._bus = bus
        self._session_dir = session_dir
        self._session_id = session_id

    def _write_summary(self, text: str) -> None:
        try:
            self._session_dir.mkdir(parents=True, exist_ok=True) # 获取会话路径
            path = self._session_dir / f"summary_{_ts_compact()}.md"
            path.write_text(text, encoding="utf-8")
        except Exception:
            logger.exception("compactor: failed to write summary file")
    async def compact(self, # 自动化，不落盘
                      context:ExecutionContext,
                      provider:LLMProvider,
                      focus:str="")->CompactionResult | None:
        result = await self.compact_messages(messages=context.messages,
                                       provider=provider,
                                       focus=focus)
        if result is None: # 没有压缩结果直接返回
            return None

        context.messages = [
            {"role": "user", "content": result.summary_text},
            {"role": "assistant", "content": "Understood, I'll continue from this summary."},
        ]
        self._write_summary(result.summary_text)
        await self._bus.publish(
            ContextCompactedEvent(
                session_id=self._session_id,
                run_id=context.run_id,
                original_tokens=result.original_token_estimate,
                summary_tokens=result.summary_tokens,
                ts=_now(),
            )
        )
        logger.info(
            "context compacted session=%s run=%s original≈%d summary=%d tokens",
            self._session_id, context.run_id,
            result.original_token_estimate, result.summary_tokens,
        )
        return result
    async def compact_messages(self, # 手动调，落盘
                               messages:List[Any], # 原始的上下文
                               provider:LLMProvider, # 用来压缩的大模型。
                               focus:str = "",# 特别交代给大模型要额外注意的信息。
                               )->CompactionResult|None: # 压缩成功返回压缩结果，压缩失败返回None
        original_estimate = sum(
            len(str(m.get("content", ""))) for m in messages
        ) // 4  # S6粗略 token 估算（字符数 / 4）
        history_text = _messages_to_text(messages) # S6将消息的列表变成大模型能读取的纯文本
        prompt = _COMPACT_PROMPT
        if focus.strip():
            prompt += f"\n\nIMPORTANT: Pay special attention to: {focus.strip()}" # S6给压缩提示词加上值得注意的部分
        compress_request: list[dict[str, object]] = [
            {"role": "user", "content": f"{prompt}\n\n---\n\n{history_text}"}
        ]
        try:
            silent_bus = EventBus() # 由于provider.chat刚需EventBus，但又不需要真的把压缩后的文本发给用户，所以搞一个没有订阅事件的bus
            response = await provider.chat(
                messages=compress_request,
                tool_schemas=[],
                bus=silent_bus,
                run_id="compact",
                step=0,
                system=prompt,
            )
        except Exception:
            logger.exception("compactor: LLM call failed, skipping compaction")
            return None
        summary_text = response.text.strip()
        if not summary_text:
            logger.warning("compactor: LLM returned empty summary, skipping compaction")
            return None

        summary_tokens = response.usage.output_tokens if response.usage else len(summary_text) // 4

        return CompactionResult(
            summary_text=summary_text,
            original_token_estimate=original_estimate,
            summary_tokens=summary_tokens,
        )

def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(f"[{role}]\n{content}")
        elif isinstance(content, list):
            blocks: list[str] = []
            for block in content:
                btype = block.get("type", "")
                if btype == "text":
                    blocks.append(block.get("text", ""))
                elif btype == "tool_use":
                    blocks.append(
                        f"<tool_call name={block.get('name')} id={block.get('id')}>\n"
                        f"{block.get('input', {})}\n</tool_call>"
                    )
                elif btype == "tool_result":
                    blocks.append(
                        f"<tool_result id={block.get('tool_use_id')}>\n"
                        f"{block.get('content', '')}\n</tool_result>"
                    )
            parts.append(f"[{role}]\n" + "\n".join(blocks))
    return "\n\n".join(parts)