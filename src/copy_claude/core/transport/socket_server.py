from __future__ import annotations

import asyncio
import logging
import json
from datetime import datetime,UTC
from contextvars import ContextVar
from pydantic import BaseModel,ValidationError
from collections.abc import Awaitable, Callable # ?
from typing import Any
from copy_claude.core.transport.ipc_broadcaster import IpcEventBroadcaster
from copy_claude.core.trace.record import TraceRecord
from copy_claude.core.trace.writer import TraceWriter # 它的用途应该是追踪写程序是否成功写出数据。
from copy_claude.core.bus.envelope import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    INVALID_PARAMS,
    HandlerError,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcSuccess,
    make_error,
)
_MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MB per frame，兼容 MCP 大文件工具结果

type CommandHandler = Callable[[dict[str, Any]], Awaitable[Any]]
# 这行代码定义了一个类型别名（Type Alias），用于描述一类特定的函数——即命令处理函数（Command Handler）。
logger = logging.getLogger(__name__)

def _now() -> str:
    return datetime.now(UTC).isoformat() # 返回目前的时间

'''-----------------------------------------获取连接的写程序----------------------------------------------------'''
_writer_var: ContextVar[asyncio.StreamWriter] = ContextVar("_writer_var") # 每一个协程的上下文变量。可以用来存信息

def get_connection_writer():
    return _writer_var.get()

class SocketServer:
    def __init__(self,
                 host:str,
                 port:int,
                 broadcaster: IpcEventBroadcaster | None = None,
                 trace: TraceWriter | None = None,
    ):
        self._host = host # 初始化端口号
        self._port = port
        self._handlers: dict[str, CommandHandler] = {} # 处理命令的保留字典，
        self._active_writers: set[asyncio.StreamWriter] = set() # 代表目前被激活的写程序们
        self._server: asyncio.AbstractServer | None = None
        self._broadcaster = broadcaster # ？
        self._trace = trace # 异步执行磁盘写入记录的程序

    def register(self, method:str,handler: CommandHandler)->None:
        self._handlers[method] = handler

    async def start(self)->str:
        try: # 尝试启动服务端端口查看是否连接，如果连接成功说明当前已经运行在host:port端口。这是一个检查是否联网成功的好方法。
            # 启动监听前会先探活一次
            _r, w = await asyncio.open_connection(self._host, self._port)
            w.close()
            await w.wait_closed()
            raise SystemExit(f"core already running at {self._host}:{self._port}") # 探活成功，不必新启动客户端
        except (ConnectionRefusedError, OSError):
            pass
        # 探活失败，当前没有服务端启动，需要新开一个服务端
        self._server = await asyncio.start_server( # start核心为此函数。？
            self._handle_connection,
            host=self._host,
            port=self._port,
            limit=_MAX_LINE_BYTES,
        )
        return f"{self._host}:{self._port}"

    # 关闭服务器：先断开所有活跃连接，再等待服务器完全关闭（最多 2 秒）
    async def stop(self)->None:
        if self._server is None:
            return
        for writer in self._active_writers:
            try:
                writer.close()
            except Exception:
                pass
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

    # 处理单个客户端连接，完成后关闭写流
    async def _handle_connection( # 处理链接程序
            self,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername", "<unknown>") # ？
        logger.debug("client connected: %s", peer) # 客户端已连接的写程序。
        self._active_writers.add(writer) # 保存已激活的写程序
        try:
            await self._read_loop(reader, writer) # 读循环？
        finally:
            self._active_writers.discard(writer) # discard类似清除吧
            if self._broadcaster is not None: # 如果有广播器
                self._broadcaster.unsubscribe(writer) # 广播器清除掉掉线的writer
            try:
                writer.close()
            except Exception:
                pass
            logger.debug("client disconnected: %s", peer)

    async def _read_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await reader.readline() # 挂起读程序的读行程序，一旦读到内容就恢复执行，继续向下走
            if not line:
                return
            await self._handle_line(line, writer) # 挂起处理输入的程序，这个无限循环实现用户-Agent反复交互。

    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        # line是用户发来的信息字节形式，序列化，应该处理一下，得到结果用写程序写内容传回用户。
        try:
            raw = json.loads(line) # 这里的line是被序列化的命令，格式是jsonrpc2.0
        except json.JSONDecodeError as e:
            await self._send(writer, make_error(None, PARSE_ERROR, f"Parse error: {e}")) # 涉及网络收发信息就得异步挂起等待
            return

        # 程序执行到这里说明line解码成功。line应该是jsonrpc2.0编码的请求，
        try:
            req = JsonRpcRequest.model_validate(raw) # 验证格式是否正确。
        except ValidationError as e:
            await self._send(writer, make_error(None, INVALID_REQUEST, "Invalid Request", str(e)))
            return
        # 程序执行到此说明格式验证正确，具有id\method\params三条有效信息。
        handler = self._handlers.get(req.method) # 根据收到的用户指令确定handler，如命令行ping这里method就是core.ping
        if handler is None: # 没找到对应的命令处理器，应该报错
            await self._send(
                writer,
                make_error(req.id, METHOD_NOT_FOUND, f"Method not found: {req.method}"),
            )
            return

        # 程序执行到此，说明找到了对应命令的处理办法，handler要承接req里的params作为参数
        try: # 新加的内容
            _writer_var.set(writer) # 在协程上下文中存储writer，就可以用函数get_connection_writer函数中得到它。
            result = await handler(req.params) # 在调用协程前存储信息。
        except ValidationError as e:
            await self._send(writer, make_error(req.id, INVALID_PARAMS, "Invalid params", str(e)))
            return
        except Exception:
            await self._send(writer, make_error(req.id, INTERNAL_ERROR, "Internal error"))
            return

        await self._send(writer, JsonRpcSuccess(id=req.id, result=result.model_dump()))


    async def _send(self, writer: asyncio.StreamWriter, msg:BaseModel) -> None: # 发送信息函数
        writer.write(msg.model_dump_json().encode()+b'\n')
        await writer.drain()
        # 程序走到这里已经将该发送的信息发送完毕。
        if self._trace is not None: # _trace是TraceWriter，负责将构造符合TraceRecord的记录保存到指定路径
            kind = "error" if isinstance(msg, JsonRpcError) else "response"
            client_id = str(writer.get_extra_info("peername", "<unknown>"))
            # 这段代码用于获取异步网络连接中客户端的地址信息（通常是 IP 地址和端口号），并将其转换为字符串作为客户端的唯一标识符。
            # 假设客户端从 192.168.1.100:12345 连接过来,client_id的值为 "('192.168.1.100', 12345)"
            self._trace.emit(
                TraceRecord(
                    ts=_now(),
                    kind = kind,
                    direction = "CORE→CLIENT",
                    layer = "ipc",
                    client_id = client_id,
                    data=msg.model_dump(),
                )
            )