from __future__ import annotations
import asyncio
from typing import Any
from collections.abc import Callable,Awaitable
type EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
from copy_claude.core.bus.envelope import JsonRpcRequest
import uuid
import json

_MAX_LINE_BYTES = 64 * 1024 * 1024  # 64 MB per frame，兼容 MCP 大文件工具结果
class IpcError(RuntimeError): # ipc报错类型
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code

class SocketClient:
    def __init__(self,host:str,port:int):
        self._host = host
        self._port = port
        # 客户端涉及读写器
        self._reader : asyncio.StreamReader | None = None
        self._writer : asyncio.StreamWriter | None = None
        # 客户端需要订阅事件处理器
        self._event_handlers:list[EventHandler] = []

        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {} # 根据req_id查询命令执行情况fut

    async def connect(self) -> None: # 客户端与选定端口产生连接。reader接收服务端信息，writer给服务端发请求
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port,limit=_MAX_LINE_BYTES)

    async def close(self)->None:
        if self._writer is not None:
            self._writer.close()
            try:
                await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
            except TimeoutError:
                pass

    def on_event(self,handler:EventHandler)->None: # 注册handler，用handler处理字典（事件）
        self._event_handlers.append(handler)

    async def send_command(self,method:str,params:dict[str,Any])->dict[str, Any]:
        # 发送的命令是什么格式？jsonrpc2.0，需要传递method和params
        # 先检查依赖是否存在
        if self._writer is None:
            raise RuntimeError("not connected — call connect() first")
        # 使用JsonRpcRequest生成结构化请求
        req_id = str(uuid.uuid4())
        command = JsonRpcRequest(method=method,params=params,id=req_id)
        # future,将来会完成的状态，当完成时可以将这一状态设为finish，字典形式的协程对象。
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        self._writer.write(command.model_dump_json().encode()+b'\n')
        # 在 writer.write 末尾添加 b"\n" 是因为通信协议基于行（line-based），即每条完整的 JSON-RPC 消息都以换行符 \n 作为结束标记。
        # 与读取端 readline() 配对
        # 在接收响应的一方（通常是服务器端或另一个客户端），会使用 await reader.readline() 来读取数据。

        await self._writer.drain()
        return await fut

    async def _dispatch(self,line:bytes)->None: # tcp连接会接收到两种格式的信息，需要根据信息格式区分开。
        # 第一种格式是jsonrpc2.0 第二种格式，事件推送：带 "kind": "event" 字段，是守护进程主动发来的，没有 id
        msg = json.loads(line) # 将得到的line反序列化为json
        if "jsonrpc" in msg: # 代表这是一个命令返回结果
            # 命令响应：找到等待它的 Future，完成它
            req_id = msg["id"]
            if req_id and req_id in self._pending:
                fut = self._pending.pop(req_id)
                if "error" in msg:
                    fut.set_exception(IpcError(msg["error"],msg["message"])) # fut设置字典为报错码和报错信息
                    # 在这里有报错说明服务端已经发送了错误信息。
                else:
                    fut.set_result(msg.get("result") or {})
        elif msg.get("kind") == "event":
            # 事件推送：调用所有注册的事件处理器
            event_data = msg.get("event", {})
            for handler in self._event_handlers:
                await handler(event_data)

    # 持续读取服务器消息，分发 RPC 响应到 pending future 或事件到 event handler
    async def run_event_loop(self):
        if self._reader is None: # 读取之前确定读取器（连接）是否存在
            raise RuntimeError("not connected — call connect() first")

        try:
            while True:
                try:
                    line = await self._reader.readline() # 获取单行
                except (ConnectionResetError,OSError): # 连接重置等错误直接跳出循环
                    break
                except (ValueError,asyncio.LimitOverrunError): # 值错误或超出限制错误
                    # 单行超出 limit；丢弃本行，继续读取后续消息
                    continue
                if not line: # 如果没有行跳出
                    break
                await self._dispatch(line)
        finally: # 断开连接代表完成
            for fut in self._pending.values(): # 清空self._pending，对里面所有的future都做处理
                if not fut.done():
                    fut.cancel()
            self._pending.clear()
