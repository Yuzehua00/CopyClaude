from __future__ import annotations
import asyncio
import logging
import copy_claude
import sys
from datetime import datetime,UTC
# 后端的核心，接收cli的请求并返回信息。
from copy_claude.core.config import CopyClaudeConfig,get_config
from copy_claude.core.bus.commands import PingCommand,PongResult
from copy_claude.core.logging_setup import setup_logging
from copy_claude.core.transport.socket_server import SocketServer
from typing import Dict,Any
import signal
# kama-core 的入口在 core/app.py，它加载配置、初始化日志、创建 SocketServer，然后注册 core.ping：
import time
logger = logging.getLogger(__name__)

class CoreApp: # 所有用户的命令处理器都在这里定义，真正的核心业务逻辑。
    async def _ping_handler(self,params:Dict[str:Any])->PongResult:
        cmd = PingCommand.model_validate(params) # 此种语境下传过来的是PingCommand
        logger.debug("ping from %s", cmd.client)
        return PongResult(
            server_version=copy_claude.__version__,
            uptime_ms=int((time.monotonic() - self._start_time) * 1000),
            received_at=datetime.now(UTC).isoformat(),
        )

    async def run(self)->None: # 接收前端发送的信息
        # kama-core 的入口在 core/app.py，它加载配置、初始化日志、创建 SocketServer，然后注册 core.ping：
        self._start_time = time.monotonic()
        self.config = get_config()
        setup_logging(self.config)
        # 创建SocketServer,并把CoreApp的Handler交给SocketServer
        server = SocketServer(self.config.host,self.config.port)
        server.register("core.ping",self._ping_handler)

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




# 同步入口：启动 CoreApp 事件循环
def run() -> None:
    asyncio.run(CoreApp().run())

if __name__ == '__main__':
    run()
