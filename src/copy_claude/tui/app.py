from __future__ import annotations

import json

from rich.markdown import Markdown

import asyncio
import logging
from typing import Any
# tui使用textual框架。
import textual
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.message import Message
from textual.widgets import  Static, Label, TextArea

from textual import events
from copy_claude.core.transport.socket_client import SocketClient, IpcError
from textual.containers import VerticalScroll

log = logging.getLogger(__name__)


def _preview(s: str, n: int) -> str:
    return s[:n] if len(s) > n else s


def _params_str(params: dict[str, any]) -> str:
    return json.dumps(params, ensure_ascii=False)


class ChatTextArea(TextArea):
    DEFAULT_CSS = """
        ChatTextArea {
            height: auto;
            min-height: 3;
            max-height: 12;
            border: round $surface-lighten-2;
            background: $background;
            padding: 0 1;
            margin: 1 2;
            scrollbar-size-vertical: 1;
        }
        ChatTextArea:focus {
            border: round $accent;
            background: $background;
        }
        """
    class Submitted(Message):
        def __init__(self, area) -> None:
            self.area = area
            self.value = area.text
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            if self.text.strip():
                self.post_message(self.Submitted(self))
            return
        if key in ("alt+enter", "shift+enter", "ctrl+j", "super+enter"):
            event.stop()
            event.prevent_default()
            if not self.read_only:
                self.insert("\n")
            return
        await super()._on_key(event)


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
        yield ChatTextArea(id="prompt", show_line_numbers=False)

    def _summary(self) -> str:  # 根据全文生成摘要。
        params_pre = _preview(self._params_full, 60)  # 全文的前缀。
        icon = "[bold yellow]✎[/bold yellow]"
        line = f"  {icon} [bold]{self._tool_name}[/bold]  [dim]{params_pre}[/dim]"
        if self._finished:  # 如果工具调用完成在line添加结果
            out_pre = _preview(self._output, 50)
            color = "red" if self._is_error else "dim"  # 如果错误颜色标红
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
        if not self._finished:  # 未完成直接返回
            return
        if "expanded" in self.classes:  # 已扩展改为关闭
            self.remove_class("expanded")
        else:  # 如果关闭改成扩展
            detail = self.query_one(".detail", Static)
            detail.update(
                f"[dim]params:[/dim]\n    {self._params_full}\n"
                f"[dim]output:[/dim]\n    {self._output}\n"
                f"[dim]elapsed:[/dim] {self._elapsed_ms}ms"
            )
            self.add_class("expanded")


class CopyClaudeTuiApp(App[None]):  # 这里也有处理事件的逻辑
    """KamaClaude 终端 UI：实时显示 daemon 事件流，支持断线自动重连。"""

    TITLE = "CopyClaude TUI"
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
        self._session_id: str | None = None
        self._pending_tools = {}
        self._pending_tool_blocks: dict[str, ToolCallBlock] = {}
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Label("[bold]CopyClaude[/bold]  [dim]connecting...[/dim]", id="header")
        yield VerticalScroll(id="log-view")
        yield ChatTextArea(id="prompt", show_line_numbers=False)

    def on_mount(self) -> None:
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
        prompt = self.query_one("#prompt", ChatTextArea)
        prompt.disabled = True
        prompt.border_title = "connecting..."

    async def action_quit(self) -> None: # 退出时尝试将结果发给session，如果失败也不干预直接退出
        if self._client is not None and self._session_id is not None:
            try:
                await self._client.send_command("session.close", {"session_id": self._session_id})
            except (IpcError, RuntimeError, OSError):
                self._append(Static("[yellow]warning: failed to close session[/yellow]"))
        self.exit()

    async def _do_send_message(self, content:str) -> None:
        if self._client is None:
            return
        try:
            await self._client.send_command(
                "session.send_message",
                {"session_id": self._session_id, "content": content},
            )
        except IpcError as e:
            self._busy = False
            prompt = self._prompt()
            prompt.disabled = False
            prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
            self._update_header("ready")
            self._append(Static(f"[red]send error: {e}[/red]", classes="log-line"))
    # 将输入框提交内容发送给当前 chat session
    async def on_chat_text_area_submitted(self,event:ChatTextArea.Submitted)->None:
        # 你定义的事件类：ChatTextArea.Submitted要与名字对齐。
        content = event.value.strip()
        if not content:
            return
        if self._client is None:
            self._append(Static("[yellow]connect error[/yellow]", classes="log-line"))
            return
        if self._session_id is None:
            self._append(Static("[yellow]session_id is None[/yellow]", classes="log-line"))
            return
        if self._busy:
            self._append(Static("[yellow]agent is busy[/yellow]", classes="log-line"))
            return
        self._busy = True
        prompt = event.area
        prompt.text = ""
        prompt.disabled = True
        prompt.border_title = "agent is working..."
        self._append(Static(f"[bold]>[/bold] {content}", classes="user-turn"))
        self._update_header("running")
        self.run_worker(self._do_send_message(content), name="send_message", exclusive=False)

    # 管理 SocketClient 生命周期：连接、订阅事件、断线重连
    async def _socket_loop(self):
        header = self.query_one("#header", Label)
        while True:
            client = SocketClient(self._host, self._port)
            self._client = None
            try:
                await client.connect()
            except (ConnectionRefusedError, OSError):
                self._update_header("disconnected")
                await asyncio.sleep(2)
                continue
            self._client = client
            # 端口连接成功需要展示在标题：传入状态位
            self._update_header("connecting")
            loop_task = asyncio.create_task(client.run_event_loop())  # 事件循环是为了持续的读取来自服务器的信息并进行分发。
            async def on_event(event: dict[str, Any]) -> None: # 异步函数改成同步函数的路由了，因此额外套一层异步的皮
                self._handle_event(event)
            client.on_event(on_event)
            try:
                params: dict[str, Any] = {
                    "topics": [
                        "session.*",
                        "run.*",
                        "step.*",
                        "tool.*",
                        "llm.token",
                        "llm.usage",
                        "log.*",
                    ],
                    "scope": "global",
                }
                if self._replay_run_id is not None:
                    params["replay_from_run"] = self._replay_run_id
                await client.send_command(method="event.subscribe", params=params)
                created = await client.send_command("session.create", {"mode": "chat"})
                self._session_id = str(created["session_id"])
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = False
                    prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    prompt.focus()
                self._update_header("ready") # 没走到这里
                await loop_task
            except IpcError as e:
                header.update(f"[bold]CopyClaude[/bold]  [red]subscribe error: {e}[/red]")
            finally:
                if not loop_task.done():
                    loop_task.cancel()
                self._client = None
                self._session_id = None
                prompt = self._prompt()
                if prompt is not None:
                    prompt.disabled = True
                    prompt.border_title = "disconnected, retrying..."
                self._break_llm()
                await client.close()
            self._update_header("disconnected")
            await asyncio.sleep(2)

    # 安全获取输入框，便于组件测试中未挂载时跳过 UI 操作
    def _prompt(self) -> ChatTextArea | None:
        try:
            return self.query_one("#prompt", ChatTextArea)
        except NoMatches:
            return None

    def _handle_event(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")

        if t == "llm.token":
            token = event.get("token", "")
            if self._current_llm is None:
                llm_block = LLMStreamBlock()
                self._append(llm_block)
                self._current_llm = llm_block
            self._current_llm.append_token(token)
            return

        self._break_llm()

        if t == "session.waiting_for_input":
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = False
                prompt.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                prompt.focus()
            self._update_header("ready")

        elif t == "session.closed":
            self._busy = False
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.border_title = "session closed"
            self._update_header("disconnected")

        elif t == "run.started":
            run_id = event.get("run_id", "")
            goal = event.get("goal", "")
            self._append(Static(
                f"[dim]run[/dim]  [cyan]{run_id}[/cyan]  [dim]{_preview(goal, 96)}[/dim]",
                classes="run-header",
            ))

        elif t == "step.started":
            step = event.get("step", "")
            self._append(Static(
                f"[dim]step {step}[/dim]",
                classes="step-divider",
            ))

        elif t == "tool.call_started":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            params = event.get("params") or {}
            tc_block = ToolCallBlock(tool_name, params)
            self._pending_tool_blocks[tool_use_id] = tc_block
            self._append(tc_block)

        elif t == "tool.call_finished":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            output = str(event.get("output") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(output, elapsed_ms)

        elif t == "tool.call_failed":
            tool_use_id = str(event.get("tool_use_id", ""))
            elapsed_ms = int(event.get("elapsed_ms") or 0)
            error_msg = str(event.get("error_message") or "")
            if tool_use_id in self._pending_tool_blocks:
                tc_done = self._pending_tool_blocks.pop(tool_use_id)
                tc_done.set_result(error_msg, elapsed_ms, is_error=True)

        elif t == "run.finished":
            status = event.get("status", "")
            steps = event.get("steps", 0)
            reason = event.get("reason") or ""
            if status == "success":
                self._append(Static(
                    f"[bold green]✓ completed[/bold green]  [dim]{steps} steps[/dim]",
                    classes="run-ok",
                ))
            else:
                detail = f"  [dim]{reason}[/dim]" if reason else ""
                self._append(Static(
                    f"[bold red]✗ failed[/bold red]{detail}  [dim]{steps} steps[/dim]",
                    classes="run-err",
                ))

        elif t == "llm.usage":
            self._append(Static(
                f"[dim]  tokens  "
                f"in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache={event.get('cache_read_input_tokens')}[/dim]",
                classes="usage",
            ))

        elif t == "log.line":
            level = event.get("level", "INFO")
            color = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "dim")
            self._append(Static(
                f"[{color}]{level}[/{color}]  "
                f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                classes="log-line",
            ))

    # def _handle_event_inner(self, event: dict[str, Any]) -> None:
    #     event_type = event.get("type")
    #
    #     if event_type == "llm.token":
    #         token = event.get("token", "")
    #         if self._current_llm is None:
    #             llm_block = LLMStreamBlock()
    #             self._append(llm_block)
    #         self._current_llm.append_token(token)
    #         return
    #
    #     self._flush_tokens(log)  # 非 token 事件来了，先把缓冲区写出去
    #
    #     if event_type == "run.started":
    #         log.write(f"[bold blue]▶ run[/bold blue]  {event.get('run_id')}  {event.get('goal')}")
    #     elif event_type == "run.finished":
    #         s = event.get("status", "")
    #         color = "green" if s == "success" else "red"
    #         log.write(f"[{color}]■ run[/{color}]  {s}  {event.get('steps')} steps")

    def _break_llm(self) -> None:
        if self._current_llm is not None:
            self._current_llm.finalize_markdown()
        self._current_llm = None

    def _append(self, widget: Widget) -> None:
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.mount(widget)
        log_view.scroll_end(animate=False)

    def _update_header(self, state:str)->None:
        try: # 确认header是否存在，如不存在直接返回
            header = self.query_one("#header", Label)
        except NoMatches:
            return
        session = f"  [dim]{self._session_id}[/dim]" if self._session_id else ""
        color = {
            "ready": "green",
            "running": "yellow",
            "disconnected": "red",
            "connecting": "dim",
        }.get(state, "dim")
        header.update(
            f"[bold]CopyClaude[/bold]  [dim]{self._host}:{self._port}[/dim]"
            f"{session}  [{color}]{state}[/{color}]"
        )
