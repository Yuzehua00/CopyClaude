from __future__ import annotations

import asyncio
from typing import Any
import sys
from copy_claude.core.config import CopyClaudeConfig
from copy_claude.core.transport.socket_client import SocketClient,IpcError

# 持续对话命令。需要打印、需要与后端收发信息。
_DECISION_MAP: dict[str, str] = {
    "y": "allow_once",
    "a": "always_allow",
    "n": "deny_once",
    "d": "always_deny",
}


class ChatPrinter:
    def __init__(self) -> None:
        self._inline = False  # 表示当前是否是文本打印
        self.pending_permission_id: str | None = None

    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    async def handle(self, event: dict[str, Any]) -> None:  # 从后端收到的事件，根据类型路由。
        t = event.get("type", "")
        if t == "llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True
        elif t == "tool.call_started":
            self._ensure_newline()
            print(f"[tool] {event.get('tool_name', '')}")
        elif t == "permission.requested":
            tool_name = str(event.get("tool_name", ""))
            param_preview = str(event.get("param_preview", ""))
            tool_use_id = str(event.get("tool_use_id", ""))
            print(f"[permission] {tool_name}  {param_preview}")
            print("  y=allow once  a=always allow  n=deny once  d=always deny")
            self.pending_permission_id = tool_use_id
        elif t == "session.waiting_for_input":
            self._ensure_newline()
            self.pending_permission_id = None
            print("[waiting for input]")
        elif t == "session.closed":
            self._ensure_newline()
            print("session closed.")


# 在线程池中读取 stdin，避免阻塞 socket event loop
async def _readline(prompt: str) -> str:  #等待线程池里的 input 执行完毕，直接返回用户输入的字符串。
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# 异步核心：创建 chat session，循环读取用户输入并发送到 daemon；权限请求时优先处理审批
async def _chat_async(config: CopyClaudeConfig) -> int:
    client = SocketClient(host=config.host, port=config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1
    # 用户的输入加回车是要write的信息，回馈的事件是read的信息。
    printer = ChatPrinter()
    client.on_event(printer.handle)
    loop = asyncio.create_task(client.run_event_loop())  # loop代表一个run的循环，并用注册的printer打印信息。
    try:
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["session.*", "run.*", "tool.*", "llm.token", "permission.*"],
                "scope": "global",
            },
        )
        created = await client.send_command("session.create", {"mode": "chat"})  # 创建会话，多次交流用到，返回id
        session_id = str(created["session_id"])
        print(f"[session: {session_id}]")
        while True: # 多轮对话的基础，永远等待用户的输入，除非用户ctrl+c退出。
            try:
                line = await _readline("> ")  # 从线程池读取输入。目的是为了防止阻塞循环
            except (EOFError, KeyboardInterrupt):  # 唯一的退出方式是报错或键入中断，否则一直读行。
                break
            content = line.strip() # 读到的行
            if not content:
                continue
            if printer.pending_permission_id: # 有工具调用权限请求。
                decision = _DECISION_MAP.get(content.lower(), None)
                if decision is None:
                    print("  输入 y (允许此次请求), a (无视风险一直允许), "
                          "n (拒绝此次请求), d (一直拒绝)")
                    continue
                tool_use_id = printer.pending_permission_id
                printer.pending_permission_id = None
                await client.send_command("permission.respond",
                                          {"tool_use_id": tool_use_id, "decision": decision},)
                continue
            await client.send_command(
                "session.send_message",
                {"session_id": session_id, "content": content},
            )
        await client.send_command("session.close", {"session_id": session_id})
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally: # 取消loop，关闭连接
        loop.cancel()
        try:
            await loop
        except asyncio.CancelledError:
            pass
        await client.close()
    return 0

def cmd_chat(config:CopyClaudeConfig)->None:
    try:
        exit_code = asyncio.run(_chat_async(config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)
