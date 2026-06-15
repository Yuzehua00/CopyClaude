from __future__ import annotations

import asyncio
import sys
import json
import time
# from pydantic import BaseModel
from typing import Any
# from copy_claude.core.runner import AgentRunner
from copy_claude.core.config import CopyClaudeConfig
# from copy_claude.core.bus.events import LlmTokenEvent,RunFinishedEvent,ToolCallStartedEvent
from copy_claude.core.transport.socket_client import SocketClient,IpcError


def cmd_run(goal: str, config: CopyClaudeConfig) -> None:  # 同步入口
    # 与llm交互需要打印终端输出，启动AgentLoop，因此要制作AgentRunner和StdoutPrinter
    # 此处没有分离进程，由客户端直接调用AgentRunner，这不是实际情况，实际情况应该是cli与daemon以网络形式联系。
    try:
        exit_code = asyncio.run(_run_async(goal, config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)


async def _run_async(goal: str, config: CopyClaudeConfig) -> int:  # 网络连接版
    # 1创建客户端，与配置指定端口连接
    client = SocketClient(config.host, config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1
    printer = StdoutPrinter()  # 展示前端
    finished = asyncio.Event()  # 标志着run事件结束。
    # asyncio.Event() 是 asyncio 里的信号量：finished.set() 把它置位，await finished.wait() 会阻塞到置位为止。
    # 这里用来等 run.finished 事件：收到了就置位，_run_async 就能退出。
    exit_code = 0  # 用于区分是成功运行还是失败运行

    # 由于现在采用网络连接收发命令，因此得到的是字符串而非原本的Event，交给printer.handle的event应该是字典
    async def on_event(event: dict[str, Any]) -> None:  # event_handler,解析到来的事件并给printer打印。
        nonlocal exit_code
        await printer.handle(event)
        if event["type"] == "run.finished":
            exit_code = 0 if event["status"] == "success" else 1  # 如果收到结束事件，将根据事件状态确定退出码
            finished.set()  # 将事件置位，订阅该事件的waiter将启动。

    # 为什么client需要on_event函数？on_event函数用于注册服务器推送事件的回调，可多次调用以添加多个 handler
    client.on_event(on_event)
    loop_task = asyncio.create_task(client.run_event_loop())
    # 2发送 event.subscribe 命令，与守护进程通信告知需要订阅哪些事件。client需要函数send_command
    # 目前两个命令，agent.run和event.subscribe
    try:
        await client.send_command(method="event.subscribe", params={
            "topics": ["run.*", "step.*", "tool.*", "llm.token", "llm.usage"],
            "scope": "global",
        })  # 告知daemon需要订阅哪些事件。需要通知事件的type字段。
        await client.send_command("agent.run", {"goal": goal})  # 发送agent运行命令，并提供用户目标。
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        loop_task.cancel()
        await client.close()
        return 1

    await finished.wait()
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    await client.close()
    return exit_code


class StdoutPrinter:  # 订阅EventBus，把所有的事件都保存下来。
    def __init__(self) -> None:
        self._inline = False
        self._run_start: float = 0.0

    def _ensure_newline(self) -> None:  # 保证换行
        if self._inline:
            print()
            self._inline = False

    async def handle(self, event: dict[str:Any]) -> None:
        t = event.get("type")

        if t == "run.started":
            self._run_start = time.monotonic()
            print(f"[run] {event.get('run_id', '')}")

        elif t == "step.started":
            self._ensure_newline()
            print(f"[step {event.get('step')}] planning...")

        elif t == "llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True

        elif t == "tool.call_started":
            self._ensure_newline()
            params_str = json.dumps(event.get("params", {}), ensure_ascii=False)
            print(f"[tool] {event.get('tool_name', '')} {params_str}")

        elif t == "tool.call_finished":
            print(f"[tool] {event.get('tool_name', '')} ✓  {event.get('elapsed_ms')}ms")

        elif t == "tool.call_failed":
            print(
                f"[tool] {event.get('tool_name', '')} ✗  {event.get('error_message', '')}",
                file=sys.stderr,
            )

        elif t == "step.finished":
            self._ensure_newline()
            print(f"[step {event.get('step')}] done")

        elif t == "run.finished":
            self._ensure_newline()
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.get('status', '')}  {event.get('steps')} steps  {elapsed:.1f}s")
