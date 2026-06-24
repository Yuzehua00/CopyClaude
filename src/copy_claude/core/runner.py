import asyncio
from typing import Any,List
from pathlib import Path
from datetime import datetime,UTC
from copy_claude.core.config import CopyClaudeConfig
from copy_claude.core.runs import new_run_id,RUNS_DIR
from copy_claude.core.events.bus import EventBus,EventHandler
from copy_claude.core.events.writer import EventWriter
from copy_claude.core.llm.base import LLMProvider
from copy_claude.core.llm.provider import AnthropicProvider
from copy_claude.core.tools.registry import ToolRegistry
from copy_claude.core.tools.builtin import (
ReadFileTool,
WriteFileTool,
ListDirTool,
BashTool,
TaskCreateTool,
TaskGetTool,
TaskListTool,
TaskUpdateTool
)
from copy_claude.core.loop import AgentLoop
from copy_claude.core.context import ExecutionContext
from copy_claude.core.bus.events import RunFinishedEvent, RunStartedEvent
from copy_claude.core.trace.writer import TraceWriter
from copy_claude.core.trace.provider import TraceProvider
from copy_claude.core.task.manager import TaskManager
from dataclasses import dataclass
def _now() -> str:
    return datetime.now(UTC).isoformat()

@dataclass
class RunOutcome:
    status: str
    result: str
    reason: str|None
class AgentRunner: # AgentRunner负责将AgentLoop需要的所有组件全都准备好
    # 最需要的是llm，工具集合，循环控制器，观测器（采用事件广播形式实现观测），对话历史存储
    def __init__(self,
                 config: CopyClaudeConfig,
                 *,
                 extra_handlers:List[EventHandler]|None=None,
                 bus:EventBus=None,
                 runs_dir:Path|None=None,
                 provider:LLMProvider | None = None, # 只要实现了async chat函数就可以通过静态类型检查,传入None
                 trace:TraceWriter|None=None,
                 ):
        self._config = config
        self._extra_handlers:List[EventHandler] = extra_handlers or []
        self._runs_dir = runs_dir or RUNS_DIR
        self._provider = provider
        self._bus = bus
        self._trace = trace

    async def run(self,goal:str,
                  *,
                  run_id:str=None,)->None:
        await self.run_and_capture(goal,run_id=run_id)
    def _build_registry(self,task_manager:TaskManager)->ToolRegistry:
        registry = ToolRegistry()
        # 初始化一些工具：
        for t in [ReadFileTool(),WriteFileTool(),ListDirTool(),BashTool()]:
            registry.register(t)
        for t in [
            TaskCreateTool(task_manager),
            TaskUpdateTool(task_manager),
            TaskListTool(task_manager),
            TaskGetTool(task_manager),
        ]:
            registry.register(t)
        return registry
    async def run_and_capture(self,
                  goal:str,
                  *,
                  run_id:str|None=None,)->RunOutcome:
        # 1对话记录前期准备，创建run_id并生成文件夹路径，便于存储信息。
        run_id = run_id or new_run_id()
        run_path = self._runs_dir / run_id
        run_path.mkdir(parents=True, exist_ok=True)
        task_manager = TaskManager(run_path/".tasks")

        # 2Agent循环开始前需要监听广播，所以应该实现监测器。
        # s1的监听者为EventWriter.handle，StdoutPrinter.handle，AgentRunner 传进来的 extra_handlers
        bus = self._bus if self._bus is not None else EventBus()
        for h in self._extra_handlers:  # StdoutPrinter 从这里进来,stdoutPrinter是订阅者。发出者是这个bus
            bus.subscribe(h)
        # 4工作记忆，即对话记录
        context = ExecutionContext(run_id=run_id, goal=goal, max_steps=self._config.agent.max_steps)
        # 5将上下文事件记录的脚本开启。
        # 修改顺序，即使 LLM provider 初始化失败，客户端也已经收到了 run.started，而不是一直等待什么都不知道。
        async with EventWriter(run_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await bus.publish(RunStartedEvent(run_id=run_id, goal=goal, ts=_now()))
            cancelled = False
            try:
                # 3正式循环，需要llm（provider）,工具箱，循环控制器
                registry = self._build_registry(task_manager)
                provider = self._provider or AnthropicProvider(self._config.llm.default_model)  # provider为后者
                if self._trace is not None:
                    provider = TraceProvider(self._trace,provider,include_payload=self._config.trace.include_llm_payload,)
                loop = AgentLoop(provider, registry, bus)
                await loop.run(context)
            except asyncio.CancelledError:
                cancelled = True # 触发了取消也不能直接raise。而是要给bus执行publish运行结束事件。
                if not context.is_done():
                    context.mark_failed("cancelled")
            await bus.publish(RunFinishedEvent(run_id=run_id,
                                               status=context.status,
                                               steps=context.step,
                                               reason=context.reason,
                                               ts=_now()))
        if cancelled:
            raise asyncio.CancelledError
        return RunOutcome(status=context.status,
                          reason=context.reason,
                          result=context.result,)