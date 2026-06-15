from __future__ import annotations

import asyncio
import logging
from typing import Any
# tui使用textual框架。
import textual
from textual.binding import Binding
from textual.app import App,ComposeResult
from textual.widgets import RichLog,Static,Label
from copy_claude.core.transport.socket_client import SocketClient,IpcError



log = logging.getLogger(__name__)

class CopyClaudeTuiApp(App[None]): # 这里也有处理事件的逻辑
    """KamaClaude 终端 UI：实时显示 daemon 事件流，支持断线自动重连。"""

    TITLE = "KamaClaude TUI"
    BINDINGS = [Binding("q", "quit", "Quit")]
    CSS = """
        Screen { layout: vertical; }
        #status {
            height: 1;
            background: $primary;
            color: $text;
            padding: 0 1;
        }
        #log { height: 1fr; }
        """
    def __init__(self, host:str, port:int,replay_run_id: str | None = None) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._replay_run_id = replay_run_id
        self._token_buf:str = ""

    def compose(self) -> ComposeResult:
        yield Label("● connecting...", id="status")
        yield RichLog(id="log", highlight=True, markup=True)
    def on_mount(self) -> None:
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
    async def _socket_loop(self):
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Label)
        while True:
            client = SocketClient(self._host, self._port)
            try:
                await client.connect()
            except (ConnectionRefusedError,OSError):
                status.update("端口连接失败，等待两秒后重新连接")
                await asyncio.sleep(2)
                continue

            status.update("端口连接成功")
            self._client = client
            loop_task = asyncio.create_task(client.run_event_loop()) # 事件循环是为了持续的读取来自服务器的信息并进行分发。
            client.on_event(lambda event: self._handle_event(event,log))
            try:
                params: dict[str, Any] = {
                    "topics": [
                        "run.*", "step.*", "tool.*",
                        "llm.token", "llm.usage", "log.*",
                    ],
                    "scope": "global",
                }
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                await client.send_command(method="event.subscribe",params=params)
                await loop_task
            except IpcError as e:
                status.update(f"● subscribe error — {e}")
            finally:
                self._flush_tokens(log)
                await client.close()
            status.update("● disconnected — retrying in 2s")
            await asyncio.sleep(2)



    async def _handle_event(self,event:dict[str, Any],log:RichLog) -> None:
        event_type = event.get("type")

        if event_type == "llm.token":
            self._token_buf += event.get("token","") # 在tui将token存在一起的理由，如果一个字一个字输出，会闪的很频繁。
            return

        self._flush_tokens(log)  # 非 token 事件来了，先把缓冲区写出去

        if event_type == "run.started":
            log.write(f"[bold blue]▶ run[/bold blue]  {event.get('run_id')}  {event.get('goal')}")
        elif event_type == "run.finished":
            s = event.get("status", "")
            color = "green" if s == "success" else "red"
            log.write(f"[{color}]■ run[/{color}]  {s}  {event.get('steps')} steps")


    def _flush_tokens(self,log:RichLog):
        if self._token_buf != "":
            log.write(self._token_buf)
            self._token_buf = ""