from __future__ import annotations
import asyncio
import json
import logging
import copy_claude
from pathlib import Path
import sys
import fnmatch
from pydantic import BaseModel
from datetime import datetime, UTC
# 后端的核心，接收cli的请求并返回信息。
from copy_claude.core.config import CopyClaudeConfig, get_config
from copy_claude.core.bus.commands import (
    PingCommand,
    PongResult,
    EventSubscribeCommand,
    EventSubscribeResult,
    AgentRunCommand,
    AgentRunResult
)
from copy_claude.core.bus.envelope import EventPushEnvelope
from copy_claude.core.logging_setup import setup_logging
from copy_claude.core.transport.socket_server import SocketServer, get_connection_writer
from copy_claude.core.events.bus import EventBus
from copy_claude.core.events.writer import EventWriter
from copy_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster
from copy_claude.core.runs import events_file, new_run_id
from copy_claude.core.runner import AgentRunner
from copy_claude.core.trace.writer import TraceWriter
from copy_claude.core.trace.record import TraceRecord
from typing import Dict, Any
import signal
# kama-core 的入口在 core/app.py，它加载配置、初始化日志、创建 SocketServer，然后注册 core.ping：
import time

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CoreApp:  # 所有用户的命令处理器都在这里定义，真正的核心业务逻辑。
    def __init__(self) -> None:
        self._bus = EventBus()
        # 事件广播器推送机制与EventWriter并列，一旦事件发生，EventWriter写下来，广播器推送到网络。如此说广播器也该有handle函数。
        self._IpcEventBroadcaster = IpcEventBroadcaster()
        self._bus.subscribe(self._IpcEventBroadcaster.handle)  # 订阅事件广播器的处理事件函数。
        self._running_runs: set[asyncio.Task[None]] = set()
        self._trace: TraceWriter | None = None

    async def _ping_handler(self, params: Dict[str:Any]) -> PongResult:  # 处理ping命令
        cmd = PingCommand.model_validate(params)  # 此种语境下传过来的是PingCommand
        logger.debug("ping from %s", cmd.client)
        return PongResult(
            server_version=copy_claude.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            received_at=datetime.now(UTC).isoformat(),
        )

    async def _subscribe_handler(self, params: Dict[str:Any]) -> EventSubscribeResult:
        cmd = EventSubscribeCommand.model_validate(params)  # 验证输入之后还要获取连接的写程序。才能把正确广播给订阅事件的端口。
        writer = get_connection_writer()

        replay_count = 0
        if cmd.replay_from_run is not None:  # 如果不是None执行回放机制。未实现
            replay_count = await self._replay_events(run_id=cmd.replay_from_run,
                                                     writer=writer,
                                                     topics=cmd.topics, )
        subscribe_id = self._IpcEventBroadcaster.subscribe(writer=writer,
                                                           topics=cmd.topics,
                                                           scope=cmd.scope, )
        return EventSubscribeResult(subscribe_id=subscribe_id,
                                    replay_count=replay_count)

    async def _agent_run_handler(self, params: Dict[str:Any]) -> AgentRunResult:  # 在守护进程启动AgentRunner。
        assert self._config is not None
        cmd = AgentRunCommand.model_validate(params)
        run_id = new_run_id()
        runner = AgentRunner(config=self._config,
                             bus=self._bus,
                             trace=self._trace)
        run_task = asyncio.create_task(runner.run(goal=cmd.goal, run_id=run_id)) # 创建任务
        self._running_runs.add(run_task) # 将任务加在集合里
        run_task.add_done_callback(self._running_runs.discard) # 任务完成后调用self.running_run.discard自动清理集合。
        return AgentRunResult(run_id=run_id, )

    async def _trace_event_handler(self, event: BaseModel) -> None:
        assert self._trace is not None
        event_dict = event.model_dump()
        self._trace.emit(TraceRecord(
            ts=_now(),
            direction="CORE",
            layer="event",
            kind="event",
            run_id=event.run_id,
            data=event_dict,
        ))

    async def _replay_events(self,
                             run_id: str,
                             writer: asyncio.StreamWriter,
                             topics: list[str],
                             ) -> int:
        path = events_file(run_id)  # 得到在本地runs文件夹下的run_id的events.jsonl，但有可能找不到。
        if not path.exists():
            for candidate in Path("~/.copyclaude/sessions").expanduser().glob(
                    f"*/runs/{run_id}/events.jsonl"
            ):  # glob函数的功能和原理是什么？难道返回的是一个列表？
                path = candidate
                break
        if not path.exists():  # 去C盘找依旧失败则直接返回
            return 0

        # 程序执行到此说明成功找到路径。此时应该读取jsonl文件。
        count = 0
        for line in path.read_text().splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type: str = event.get("type", "")
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):  # fnmatch函数的功能具体是什么？
                continue
            # 执行推送程序。要有EventPushEnvelope,用writer.write发送。
            envelope = EventPushEnvelope(event=event)
            writer.write(envelope.model_dump_json().encode() + b"\n")
            count += 1
        if count:
            await writer.drain()  # 只有有count时才需要等待写程序写完。
        return count

    async def run(self) -> None:  # 接收前端发送的信息
        # kama-core 的入口在 core/app.py，它加载配置、初始化日志、创建 SocketServer，然后注册 core.ping：
        self._start_time = time.monotonic()
        self._config = get_config()
        setup_logging(self._config)
        if self._config.trace.enabled: # 设置允许追踪
            trace_path = Path(self._config.trace.file).expanduser()
            self._trace = TraceWriter(trace_path)
            await self._trace.start()
            self._bus.subscribe(self._trace_event_handler)
        # 创建SocketServer,并把CoreApp的Handler交给SocketServer,S3追加trace
        server = SocketServer(self._config.host, self._config.port,trace=self._trace)  # 只要涉及新命令就要在CoreApp.run()增加指令
        server.register("core.ping", self._ping_handler)
        server.register("agent.run", self._agent_run_handler)
        server.register("event.subscribe", self._subscribe_handler)
        # S3广播器追加trace
        self._broadcaster = IpcEventBroadcaster(trace=self._trace)
        self._bus.subscribe(self._broadcaster.handle)

        addr = await server.start()
        logger.info("Agent核心 %s 正在监听的ip地址为=%s", copy_claude.__version__, addr)

        # 下面代码的作用是：监听操作系统的终止信号（SIGINT 即 Ctrl+C，SIGTERM 即 kill 命令），
        # 当收到信号时设置一个 asyncio.Event，从而让 await shutdown.wait() 结束阻塞，继续执行后面的 await server.stop() 来优雅关闭服务器。
        # 这是典型的异步程序中的优雅退出机制：主协程等待 shutdown 事件，信号处理器触发该事件，
        # 使程序有机会清理资源（停止服务器、关闭连接、保存状态等）再退出。只适用于Linux/Unix
        if sys.platform != 'win32':
            shutdown = asyncio.Event()
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, shutdown.set)
            loop.add_signal_handler(signal.SIGTERM, shutdown.set)

            await shutdown.wait()
        else:
            try:
                # 永远等待，直到被取消
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                logger.info("收到中断信号，正在优雅关闭...")
        await server.stop()  # 确保清理
        if self._trace is not None:
            await self._trace.stop()


# 同步入口：启动 CoreApp 事件循环
def run() -> None:
    asyncio.run(CoreApp().run())


if __name__ == '__main__':
    run()
