from __future__ import annotations

import json

from rich.markdown import Markdown

import asyncio
import logging
from typing import Any
# tui使用textual框架。
import textual
from textual.binding import Binding
from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog, Static, Label
from copy_claude.core.transport.socket_client import SocketClient, IpcError
from textual.containers import VerticalScroll

log = logging.getLogger(__name__)


def _preview(s: str, n: int) -> str:
    return s[:n] if len(s) > n else s


def _params_str(params: dict[str, any]) -> str:
    return json.dumps(params, ensure_ascii=False)


class LLMStreamBlock(Static):  # 用于在一个Widget里显示文字。而不是一个token创建一个Widget显示。
    def __init__(self):
        super().__init__("")
        self._text: str = ""
        self._finalized: bool = False

    def append_token(self, token: str) -> None:
        self._text += token
        self.update(self._text)

    def finalize_markdown(self) -> None:
        self._finalized = True
        if self._text.strip():
            self.update(Markdown(self._text))


class ToolCallBlock(Widget):
    DEFAULT_CSS = """
        ToolCallBlock { height: auto; padding: 0 2; color: $text-muted; }
        ToolCallBlock > .detail { display: none; padding: 0 2 0 4; color: $text-muted; }
        ToolCallBlock.expanded > .detail { display: block; }
        """

    # 样式设计：默认状态（折叠）：height: auto 自适应高度，只显示摘要行。内部的 .detail（详情区域）被 display: none 隐藏。
    # 展开状态：当父容器拥有 expanded 类时，.detail 变为 display: block 显示出来。
    # 颜色：使用 $text-muted（柔和的灰色），表明这些是辅助性日志信息。
    def __init__(self, tool_name: str, params: dict[str, Any]) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._params = params
        self._params_full: str = _params_str(self._params)
        self._output = ""
        self._elapsed_ms = 0
        self._is_error = False
        self._finished = False

    def compose(self) -> ComposeResult:
        yield Static(self._summary(), classes="summary")
        yield Static("", classes="detail")

    def _summary(self) -> str:  # 根据全文生成摘要。
        params_pre = _preview(self._params_full,60) # 全文的前缀。
        icon = "[bold yellow]✎[/bold yellow]"
        line = f"  {icon} [bold]{self._tool_name}[/bold]  [dim]{params_pre}[/dim]"
        if self._finished: # 如果工具调用完成在line添加结果
            out_pre = _preview(self._output, 50)
            color = "red" if self._is_error else "dim" # 如果错误颜色标红
            hint = "  [dim]▸ click to expand[/dim]" if len(self._output) > 50 else ""
            line += (
                f"\n  [dim]↳[/dim] [{color}]{out_pre}[/{color}]"
                f"  [dim]{self._elapsed_ms}ms[/dim]{hint}"
            )
        return line

    def set_result(self, output: str, elapsed_ms: int, *, is_error: bool = False) -> None:
        self._output = output
        self._elapsed_ms = elapsed_ms
        self._is_error = is_error
        self._finished = True
        if self.children:
            self.query_one(".summary", Static).update(self._summary())

        # 点击时切换展开/折叠状态
    def on_click(self) -> None:
        if not self._finished: # 未完成直接返回
            return
        if "expanded" in self.classes: # 已扩展改为关闭
            self.remove_class("expanded")
        else: # 如果关闭改成扩展
            detail = self.query_one(".detail", Static)
            detail.update(
                f"[dim]params:[/dim]\n    {self._params_full}\n"
                f"[dim]output:[/dim]\n    {self._output}\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )
            self.add_class("expanded")
class CopyClaudeTuiApp(App[None]):  # 这里也有处理事件的逻辑
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

    def __init__(self, host: str, port: int, replay_run_id: str | None = None) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._replay_run_id = replay_run_id
        self._token_buf: str = ""
        self._current_llm: LLMStreamBlock | None = None
        self._pending_tools = {}

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
            except (ConnectionRefusedError, OSError):
                status.update("端口连接失败，等待两秒后重新连接")
                await asyncio.sleep(2)
                continue

            status.update("端口连接成功")
            self._client = client
            loop_task = asyncio.create_task(client.run_event_loop())  # 事件循环是为了持续的读取来自服务器的信息并进行分发。
            client.on_event(lambda event: self._handle_event(event, log))
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
                await client.send_command(method="event.subscribe", params=params)
                await loop_task
            except IpcError as e:
                status.update(f"● subscribe error — {e}")
            finally:
                self._flush_tokens(log)
                await client.close()
            status.update("● disconnected — retrying in 2s")
            await asyncio.sleep(2)

    async def _handle_event(self, event: dict[str, Any], log: RichLog) -> None:
        self._handle_event_inner(event, log)

    def _handle_event_inner(self, event: dict[str, Any], log: RichLog) -> None:
        event_type = event.get("type")

        if event_type == "llm.token":
            token = event.get("token", "")
            if self._current_llm is None:
                llm_block = LLMStreamBlock()
                self._append(llm_block)
            self._current_llm.append_token(token)
            return

        self._flush_tokens(log)  # 非 token 事件来了，先把缓冲区写出去

        if event_type == "run.started":
            log.write(f"[bold blue]▶ run[/bold blue]  {event.get('run_id')}  {event.get('goal')}")
        elif event_type == "run.finished":
            s = event.get("status", "")
            color = "green" if s == "success" else "red"
            log.write(f"[{color}]■ run[/{color}]  {s}  {event.get('steps')} steps")

    def _flush_tokens(self, log: RichLog):
        if self._token_buf != "":
            log.write(self._token_buf)
            self._token_buf = ""

    def _break_llm(self) -> None:
        self._current_llm = None

    def _append(self, widget: Widget) -> None:
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.mount(widget)
        log_view.scroll_end(animate=False)
