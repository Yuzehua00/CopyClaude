from __future__ import annotations
from typing import Protocol
# 需要返回response
from copy_claude.core.llm.types import LlmResponse
from copy_claude.core.events.bus import EventBus
class LLMProvider(Protocol): # 未实现
    async def chat(self,
             messages: list[dict[str, any]], # 上下文，必传
             tool_schemas: list[dict[str, object]], # 工具说明，必传
             bus:EventBus, # 事件订阅器，用于记录信息，必传。
             run_id:str, # 对话标识符，必传
             *,
             step: int = 0, # 当前步数
             system: str | None = None, #未知
             )->LlmResponse:...