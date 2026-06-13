from __future__ import annotations

import asyncio
import json
import sys
import time
import copy_claude
from copy_claude.core.config import CopyClaudeConfig
from copy_claude.core.bus.commands import PongResult
from copy_claude.core.bus.envelope import JsonRpcError, JsonRpcSuccess

def cmd_ping(config: CopyClaudeConfig)->None: # 同步入口，由此进入异步事件循环。
    # 创建新事件循环，运行_ping协程。
    try:
        asyncio.run(_ping(config))
    except asyncio.TimeoutError:
        print(f"error: core ping timeout ({config.host}:{config.port})", file=sys.stderr)
        sys.exit(1)
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        sys.exit(1)

async def _ping(config: CopyClaudeConfig)->None:
    # 1.记录初始时间，发起异步tcp连接。
    # 2.构造 JSON-RPC 请求对象，包含方法名 core.ping 和参数（客户端版本）。用writer,write将该对象以行的形式发出去。
    # 等待缓冲区数据真正发送到操作系统的 TCP 栈。，需要 await。
    # 3.等待服务器返回数据，记录整个时间

    t0 = time.monotonic()
    reader,writer = await asyncio.open_connection(config.host, config.port) # 异步发起 TCP 连接到守护进程。
    # 用await，因为建立连接需要时间，但不会阻塞其他任务
    req = { # 发送内容的结构体
        "jsonrpc": "2.0",
        "id": "cli-1",
        "method": "core.ping",
        "params": {"client": f"cli/{copy_claude.__version__}"},
    }

    writer.write(json.dumps(req).encode() + b'\n') # 将 JSON 字符串加上换行符编码为字节，写入写缓冲区（立即返回，不等待真正发出）。这里涉及网络编程
    await writer.drain() # await等待缓冲区数据真正发送到操作系统的 TCP 栈。防止背压（发送太快导致缓冲区堆积），需要 await。
    # 上述操作将要发送的数据写入TCP栈。下面来接收数据判断ping值。

    line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    # reader.readline()：异步读取一行（直到 \n）。因为服务器会返回一个 JSON-RPC 响应并以换行符结束。
    # asyncio.wait_for(coro, timeout=10.0)：给 readline 加上超时限制，超过 10 秒没收到完整一行就抛出 asyncio.TimeoutError。

    delay_ms = int((time.monotonic() - t0) * 1000) # 真实收发时间。可以关闭协程了。

    writer.close()
    await writer.wait_closed() # 等待写端关闭是因为写端可能还有数据未发送完，不能直接关。await挂起

    raw = json.loads(line) # 加载接收端的收到的信息，依赖(JsonRPCError/JsonRpcSuccess)用于记录tcp收发信息/PongResult

    # CLI 发出去的是JSON，但 daemon 不能直接相信这份JSON格式是否正确。进程间传递的消息必须先经过协议模型验证。
    if "error" in raw:
        err = JsonRpcError.model_validate(raw) # 这就是继承BaseModel的理由，可以用已有的方法验证传入json是否匹配字段
        print(f"error: {err.error.code} {err.error.message}", file=sys.stderr)
        sys.exit(1)

    resp = JsonRpcSuccess.model_validate(raw)
    result = PongResult.model_validate(resp.result)
    print(f"pong server={result.server_version} uptime={result.uptime_ms}ms latency={delay_ms}ms")


