from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from pydantic import BaseModel,ConfigDict

from copy_claude.core.agents.loader import AgentProfile, AgentProfileLoader
from copy_claude.core.bus.events import SubagentStartedEvent, SubagentFinishedEvent
from copy_claude.core.context import ExecutionContext
from copy_claude.core.events.bus import EventBus
from copy_claude.core.events.writer import EventWriter
from copy_claude.core.llm.base import LLMProvider
from copy_claude.core.loop import AgentLoop
from copy_claude.core.permission.manager import PermissionManager
from copy_claude.core.runs import new_run_id
from copy_claude.core.subagent.registry import BackgroundTaskRegistry
from copy_claude.core.tools.base import BaseTool, ToolResult
from copy_claude.core.tools.builtin import ReadFileTool, BashTool, WriteFileTool, ListDirTool, TaskCreateTool, \
    TaskUpdateTool, TaskListTool, TaskGetTool
from copy_claude.core.tools.registry import ToolRegistry

_profile_loader = AgentProfileLoader()
class SpawnAgentParams(BaseModel): # 这是子agent的描述、参数、是否后台运行
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    run_in_background: bool = False
    subagent_type: str = ""

def _now() -> str:
    return datetime.now(UTC).isoformat()

class SpawnAgentTool(BaseTool):
    name = "spawn_agent"
    description = (
        "Spawn an isolated sub-agent to handle a self-contained sub-task. "
        "The sub-agent starts with a clean context containing only the provided prompt — "
        "it does not inherit the current conversation history. "
        "Use run_in_background=true to run in parallel; retrieve result later with agent_result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "3-5 word task description shown in progress display",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Complete task description including all context the sub-agent needs. "
                    "The sub-agent cannot see the parent conversation, so be explicit."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "When true, returns immediately with a run_id; use agent_result to poll.",  # noqa: E501
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent role profile (planner/executor/reviewer). Leave empty for default.",  # noqa: E501
            },
        },
        "required": ["description", "prompt"],
    }
    params_model = SpawnAgentParams

    # 构造 SpawnAgentTool；depth=0 表示根 agent，最大允许嵌套深度为 2
    def __init__(
            self,
            provider: LLMProvider,
            parent_bus: EventBus,
            parent_run_id: str,
            permission_manager: PermissionManager | None,
            max_steps: int,
            task_registry: BackgroundTaskRegistry,
            runs_dir: Path,
            session_id: str,
            depth: int = 0,
    ) -> None:
        self._provider = provider
        self._parent_bus = parent_bus
        self._parent_run_id = parent_run_id
        self._permission_manager = permission_manager
        self._max_steps = max_steps
        self._task_registry = task_registry
        self._runs_dir = runs_dir
        self._session_id = session_id
        self._depth = depth

    # 派生子 agent，前台时阻塞直到完成并返回结果，后台时立即返回 run_id
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = SpawnAgentParams.model_validate(params)

        if self._depth >= 2:
            return ToolResult(
                content="Subagent nesting limit (2) reached; cannot spawn further subagents.",
                is_error=True,
                error_type="runtime_error",
            )
        profile: AgentProfile | None = None
        if p.subagent_type: # 子Agent的种类也是子Agent的名字，用名字来获取子Agent的配置。
            profile = _profile_loader.load(p.subagent_type) # 配置

        # 子Agent需要的内容包括run_id，上下文和事件总线
        child_run_id = new_run_id()
        child_context = ExecutionContext(
            run_id=child_run_id,
            goal=p.prompt,
            max_steps=self._max_steps,
            system_prompt_override=profile.system_prompt if profile else None,
        )

        child_bus = EventBus()

        # 将子 bus 所有事件桥接到父 bus，TUI 据此渲染嵌套进度
        async def _bridge(event: BaseModel) -> None:
            await self._parent_bus.publish(event)

        child_bus.subscribe(_bridge) # 订阅_bridge，当子buspublish事件时，会传给父bus去publish。
        child_registry = self._build_child_registry(child_bus, child_run_id, profile)
        child_loop = AgentLoop(
            self._provider,
            child_registry,
            child_bus,
            permission_manager=self._permission_manager,
            session_id=self._session_id,
        )
        await self._parent_bus.publish(
            SubagentStartedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                description=p.description,
                ts=_now(),
            )
        )
        child_run_path = self._runs_dir / child_run_id
        child_run_path.mkdir(parents=True, exist_ok=True)

        if p.run_in_background: # 后台模式下，Agentloop被包装进task里。并注册在_task_registry中。
            task: asyncio.Task[None] = asyncio.create_task(
                self._run_background(
                    child_loop, child_context, child_bus, child_run_path, child_run_id
                )
            )
            self._task_registry.register(child_run_id, task, child_context)
            return ToolResult(
                content=(
                    f"Subagent started in background. run_id={child_run_id}. "
                    f"Use agent_result(run_id='{child_run_id}') to retrieve result."
                )
            )
        # 非后台模式，就在这个函数里阻塞。直到返回结果。
        async with EventWriter(child_run_path / "events.jsonl") as writer:
            writer.subscribe(child_bus)
            await child_loop.run(child_context)

        await self._parent_bus.publish(
            SubagentFinishedEvent(
                run_id=child_run_id,
                parent_run_id=self._parent_run_id,
                status=child_context.status,
                ts=_now(),
            )
        )

        if child_context.status == "success":
            return ToolResult(
                content=child_context.result or "Subagent completed with no text output."
            )
        return ToolResult(
            content=(
                    child_context.result
                    or f"Subagent failed (status={child_context.status}, reason={child_context.reason})"
            ),
            is_error=True,
            error_type="runtime_error",
        )
    def _build_child_registry(
            self,
            child_bus: EventBus,
            child_run_id: str,
            profile: AgentProfile | None,
    ) -> ToolRegistry:
        from copy_claude.core.task.manager import TaskManager
        allowed: set[str] | None = (
            set(profile.allowed_tools) if profile and profile.allowed_tools else None
        ) # 获取子Agent的许可工具名单。

        def _allowed(name: str) -> bool: # 判断子agent是否可以调用当前工具，以此决定注不注册
            return allowed is None or name in allowed

        registry = ToolRegistry()
        _all_tools = [
            ReadFileTool(),
            BashTool(),
            WriteFileTool(),
            ListDirTool(),
        ]
        for t in _all_tools:
            if _allowed(t.name):
                registry.register(t)

        # 所有agent都应该有创建、更新、删除任务的能力，因此创建TaskManager
        child_task_manager = TaskManager(self._runs_dir / child_run_id / ".tasks")
        for t in [
            TaskCreateTool(child_task_manager),
            TaskUpdateTool(child_task_manager),
            TaskListTool(child_task_manager),
            TaskGetTool(child_task_manager),
        ]:
            if _allowed(t.name):
                registry.register(t) # 注册任务相关工具

        # 如果深度允许，可以让子agent再次生成子agent。
        if self._depth < 1:
            nested = SpawnAgentTool(
                provider=self._provider,
                parent_bus=child_bus,
                parent_run_id=child_run_id,
                permission_manager=self._permission_manager,
                max_steps=self._max_steps,
                task_registry=self._task_registry,
                runs_dir=self._runs_dir,
                session_id=self._session_id,
                depth=self._depth + 1,
            )
            if _allowed("spawn_agent"):
                registry.register(nested)
            if _allowed("agent_result"):
                registry.register(AgentResultTool(self._task_registry))

        return registry

    # 后台任务协程：写事件文件，运行 loop，发布完成事件
    async def _run_background(
            self,
            loop: AgentLoop,
            context: ExecutionContext,
            bus: EventBus,
            run_path: Path,
            run_id: str,
    ) -> None: #
        async with EventWriter(run_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await loop.run(context)
        await self._parent_bus.publish(
            SubagentFinishedEvent(
                run_id=run_id,
                parent_run_id=self._parent_run_id,
                status=context.status,
                ts=_now(),
            )
        )
class AgentResultParams(BaseModel):
    run_id: str
class AgentResultTool(BaseTool): # 用这个工具获取后台运行的子Agent结果。
    name = "agent_result"
    description = (
        "Retrieve the result of a background sub-agent previously started with spawn_agent. "
        "Returns 'still running' if the sub-agent has not yet completed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The run_id returned by spawn_agent(run_in_background=true)",
            },
        },
        "required": ["run_id"],
    }
    params_model = AgentResultParams

    # 初始化，持有共享的后台任务注册表
    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        self._task_registry = task_registry # 用runid获取asyncio.task和上下文。

    # 查询指定 run_id 的后台任务状态，返回结果或错误
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = AgentResultParams.model_validate(params) # 输入的只有run_id
        entry = self._task_registry.get(p.run_id)
        if entry is None: # run_id查不到，报错
            return ToolResult(
                content=f"Unknown run_id: {p.run_id}. Only background subagents can be queried.",
                is_error=True,
                error_type="runtime_error",
            )
        task, context = entry
        if not task.done():
            return ToolResult(content="still running")
        if task.cancelled():
            return ToolResult(
                content="Subagent was cancelled.", is_error=True, error_type="runtime_error"
            )
        exc = task.exception()
        if exc is not None:
            return ToolResult(
                content=f"Subagent raised an exception: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(content=context.result or "Subagent completed with no text result.")