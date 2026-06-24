from __future__ import annotations
import asyncio

from copy_claude.core.llm.base import LLMProvider
from copy_claude.core.tools.registry import ToolRegistry
from copy_claude.core.events.bus import EventBus
from copy_claude.core.context import ExecutionContext
from copy_claude.core.bus.events import StepStartedEvent, StepFinishedEvent
from copy_claude.core.tools.invocation import invoke_tool
from datetime import datetime,UTC

def _now()->str:
    return datetime.now(UTC).isoformat()
class AgentLoop:
    def __init__(self,provider:LLMProvider,registry:ToolRegistry,bus:EventBus) -> None:
        self._provider = provider # 类型检查实现chat方法的类，一般为anthropic的类，要传入env设置的环境变量的api_key
        self._registry = registry
        self._bus = bus
    async def run(self,context:ExecutionContext) -> None: # context上下文此时第一句话是用户目标
        while not context.is_done(): # 上下文没返回结束，代表循环未完成
            context.step+=1
            await self._bus.publish(StepStartedEvent(run_id = context.run_id,step=context.step,ts=_now())) # 循环正式开始，广播一个步骤开始事件
            # ── plan：让 LLM 思考下一步 ──────────────────────────
            try: # 尝试获取模型反馈，获取失败应该记录。未能获取llm的信息。
                response = await self._provider.chat(
                    messages = context.messages,
                    tool_schemas = self._registry.tool_schemas(),
                    bus = self._bus,
                    run_id=context.run_id,
                    step=context.step,
                ) # 需要给大模型提供上下文messages和可用工具，run_id和bus事件。
            except asyncio.CancelledError:
                context.mark_failed("cancelled") # 记录系统记录llm运行错误及原因。
                raise # 如果这里不挂起，那么会执行到后面，导致使用未初始化的变量response
            except Exception:
                context.mark_failed("llm_error")
                break

            # ----observe：把 LLM 响应追加到对话历史---------------
            # llm返回一个是对话信息，一个是工具调用信息。
            blocks = []
            blocks.append({"type":"text","text":response.text}) # 记录llm返回文本。
            for tc in response.tool_calls:
                blocks.append({"type":"tool_use","id":tc.id,"name":tc.name,"input":tc.input}) # 记录工具调用情况
            context.add_assistant_message(blocks)

            # ---------act,调用llm用的工具-----------------------
            if response.stop_reason=="tool_use":
                for tc in response.tool_calls:
                    # 调用工具的函数。
                    result = await invoke_tool(self._registry, tc, self._bus, context.run_id)
                    context.add_tool_result(tc.id, result.content, is_error=result.is_error)
            # ── 终止检查 ──────────────────────────────────────────
            if response.stop_reason == "end_turn":
                context.mark_success()
            elif context.step >= context.max_steps:
                context.mark_failed("exceeded_max_steps")
            await self._bus.publish(StepFinishedEvent(run_id = context.run_id,step=context.step,ts=_now()))