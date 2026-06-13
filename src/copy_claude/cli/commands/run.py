from __future__ import annotations

import asyncio
import sys
import json
import time
from pydantic import BaseModel
from copy_claude.core.runner import AgentRunner
from copy_claude.core.config import CopyClaudeConfig
from copy_claude.core.bus.events import LlmTokenEvent,RunFinishedEvent,ToolCallStartedEvent

def cmd_run(goal:str,config:CopyClaudeConfig)->None: # 同步入口
    # 与llm交互需要打印终端输出，启动AgentLoop，因此要制作AgentRunner和StdoutPrinter
    printer = StdoutPrinter()
    runner = AgentRunner(config=config, extra_handlers=[printer.handle])
    try:
        asyncio.run(runner.run(goal))
    except KeyboardInterrupt:
        sys.exit(130)

class StdoutPrinter: # 订阅EventBus，把所有的事件都保存下来。
    def __init__(self)->None:
        self._inline = False
        self._run_start: float = 0.0

    def _ensure_newline(self)->None: # 保证换行
        if self._inline:
            print()
            self._inline = False


    async def handle(self,event:BaseModel)->None:
        if isinstance(event, LlmTokenEvent):
            print(event.token, end="", flush=True)
            self._inline = True  # 还没换行，记录下来
        elif isinstance(event, ToolCallStartedEvent):
            self._ensure_newline()  # LLM 流式输出可能没有换行，先补上
            print(f"[tool] {event.tool_name} {json.dumps(event.params, ensure_ascii=False)}")

        elif isinstance(event, RunFinishedEvent):
            self._ensure_newline()
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.status}  {event.steps} steps  {elapsed:.1f}s")