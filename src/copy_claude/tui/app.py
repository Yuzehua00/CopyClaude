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
from textual.widgets import Static, Label, TextArea

from textual import events
from copy_claude.core.transport.socket_client import SocketClient, IpcError
from textual.containers import VerticalScroll

log = logging.getLogger(__name__)


def _preview(s: str, n: int) -> str:
    return s[:n] if len(s) > n else s


def _params_str(params: dict[str, any]) -> str:
    return json.dumps(params, ensure_ascii=False)


class PermissionBlock(Static):  # 这个块用来供用户选择。
    _LABEL_MAP: dict[str, str] = {
        "allow_once": "allowed (once)",
        "always_allow": "always allowed",
        "deny_once": "denied",
        "always_deny": "always denied",
        "timeout": "⏱ timed out",
    }
    LABEL_MAP = _LABEL_MAP

    class Resolved(Message):
        def __init__(self, block: PermissionBlock, decision: str) -> None:
            self.block = block
            self.decision = decision
            super().__init__()

    def __init__(self, tool_use_id: str,
                 tool_name: str,
                 param_preview: str) -> None:
        self._tool_use_id = tool_use_id
        self._tool_name = tool_name
        self._param_preview = param_preview
        self._resolved = False
        super().__init__(self._pending_text(), classes="log-line")

    def _pending_text(self) -> str:
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        return f"[bold red]? permission[/bold red]  [bold]{self._tool_name}[/bold]{preview}"

    def _resolve(self, decision: str) -> None:
        if self._resolved:
            return
        self._resolved = True
        allowed = decision in ("allow_once", "always_allow")
        icon = "[bold green]✓[/bold green]" if allowed else "[bold red]✗[/bold red]"
        label = self._LABEL_MAP.get(decision, decision)
        preview = f"  [dim]{self._param_preview}[/dim]" if self._param_preview else ""
        self.update(
            f"{icon} permission  [bold]{self._tool_name}[/bold]{preview}  [dim]{label}[/dim]"
        )
        self.post_message(self.Resolved(self, decision))


"""内联权限选择控件：挂载在日志流中，键盘焦点无需 ModalScreen。"""


class PermissionSelect(Static):
    can_focus = True
    DEFAULT_CSS = """
    PermissionSelect {
        height: auto;
        padding: 0 2;
        margin-bottom: 1;
    }
    """
    _CHOICES: tuple[tuple[str, str, str], ...] = (
        ("allow_once", "Allow once", "y / 1"),
        ("always_allow", "Always allow", "a / 2"),
        ("deny_once", "Deny", "n / 3"),
        ("always_deny", "Always deny", "d / 4"),
    )
    _KEY_MAP: dict[str, str] = {
        "y": "allow_once", "1": "allow_once",
        "a": "always_allow", "2": "always_allow",
        "n": "deny_once", "3": "deny_once",
        "d": "always_deny", "4": "always_deny",
    }

    # 用户作出权限决策时发布，携带工具 ID 和决策字符串
    class Decided(Message):
        # 初始化决策消息，存储控件引用、工具 ID 和决策
        def __init__(self, widget: PermissionSelect, tool_use_id: str, decision: str) -> None:
            self.widget = widget
            self.tool_use_id = tool_use_id
            self.decision = decision
            super().__init__()

    def __init__(self, tool_use_id: str, ):
        super().__init__("")
        self._tool_use_id = tool_use_id
        self._cursor = 0

    def on_mount(self) -> None:
        self.update(self._render_ui())
        self.focus()
        log.debug(
            "PermissionSelect.on_mount  can_focus=%s  focused_after=%r",
            self.can_focus,
            self.app.focused,
        )
        self.app.call_after_refresh(self._log_deferred_focus)

    def _log_deferred_focus(self) -> None:
        log.debug(
            "PermissionSelect.deferred_focus  app.focused=%r  has_focus=%s  focusable=%s",
            self.app.focused,
            self.has_focus,
            self.focusable,
        )

    # 焦点到达时记录，用于确认 focus() 是否真正生效
    def on_focus(self, event: events.Focus) -> None:
        log.debug("PermissionSelect.on_focus  has_focus=%s  app.focused=%r", self.has_focus, self.app.focused)

    # 焦点离开时记录，用于追踪是否被其他控件抢走焦点
    def on_blur(self, event: events.Blur) -> None:
        log.debug("PermissionSelect.on_blur  app.focused=%r", self.app.focused)

    def _render_ui(self) -> str:  # 光标对应哪个选项哪个选项就是亮的
        lines: list[str] = []
        for i, (_, label, key_hint) in enumerate(self._CHOICES):
            if i == self._cursor:
                lines.append(f"  [bold cyan]❯ {label}[/bold cyan]  [dim]{key_hint}[/dim]")
            else:
                lines.append(f"    {label}  [dim]{key_hint}[/dim]")
        lines.append("[dim]  ↑↓ navigate   enter confirm[/dim]")
        return "\n".join(lines)

    # 方向键导航；快捷键直接选择；enter 确认光标位置
    def on_key(self, event: events.Key) -> None:
        log.debug("PermissionSelect.on_key  key=%r  char=%r", event.key, event.character)
        key = event.key
        if key in ("up", "k"):
            event.stop()
            self._cursor = (self._cursor - 1) % len(self._CHOICES)
            self.update(self._render_ui())
        elif key in ("down", "j"):
            event.stop()
            self._cursor = (self._cursor + 1) % len(self._CHOICES)
            self.update(self._render_ui())
        elif key == "enter":
            event.stop()
            self._pick(self._CHOICES[self._cursor][0])
        else:
            decision = self._KEY_MAP.get(key)
            if decision is not None:
                event.stop()
                self._pick(decision)

    # 发布决策消息，由宿主 App 负责 IPC 回复和控件清理
    def _pick(self, decision: str) -> None:
        log.debug("PermissionSelect._pick  decision=%s", decision)
        self.post_message(self.Decided(self, self._tool_use_id, decision))


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
        self._pending_permission_blocks: dict[str, PermissionBlock] = {}
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

        # 生成 context 占用率的彩色进度条字符串
    def _render_ctx_bar(self, pct: float) -> str:
        filled = int(pct * 20)
        bar = "█" * filled + "░" * (20 - filled)
        label = f"ctx:{pct * 100:.1f}%"
        if pct >= 0.85:
            color = "bold red"
        elif pct >= 0.70:
            color = "yellow"
        else:
            color = "dim"
        return f"[{color}]{label} {bar}[/{color}]"
    def on_mount(self) -> None:
        self.run_worker(self._socket_loop(), exclusive=True, name="socket")
        prompt = self.query_one("#prompt", ChatTextArea)
        prompt.disabled = True
        prompt.border_title = "connecting..."

    def on_key(self, event: events.Key) -> None:
        log.debug("App.on_key  key=%r  focused=%r", event.key, self.focused)
        if not self._pending_permission_blocks:
            return
        try:
            select = self.query_one(PermissionSelect)
            if select.has_focus:
                return  # PermissionSelect 有焦点时自行处理，事件不会冒泡到这里
            key = event.key
            decision = PermissionSelect._KEY_MAP.get(key)
            if decision:
                event.stop()
                select._pick(decision)
            elif key in ("up", "k"):
                event.stop()
                select._cursor = (select._cursor - 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            elif key in ("down", "j"):
                event.stop()
                select._cursor = (select._cursor + 1) % len(PermissionSelect._CHOICES)
                select.update(select._render_ui())
            elif key == "enter":
                event.stop()
                select._pick(PermissionSelect._CHOICES[select._cursor][0])
        except Exception:
            pass

    async def on_permission_select_decided(self, msg: PermissionSelect.Decided) -> None:
        tool_use_id = msg.tool_use_id
        decision = msg.decision
        log.info("permission decided tool_use_id=%s decision=%s", tool_use_id, decision)
        try:
            msg.widget.remove()
            perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
            if perm_block is not None:
                perm_block._resolve(decision)
            if self._client is not None:
                try:
                    await self._client.send_command(
                        "permission.respond",
                        {"tool_use_id": tool_use_id, "decision": decision},
                    )
                except (IpcError, RuntimeError, OSError):
                    pass
            if not self._pending_permission_blocks:
                p = self._prompt()
                if p is not None:
                    p.disabled = False
                    p.read_only = False
                    p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                    p.focus()
        except Exception:
            log.exception("on_permission_select_decided failed tool_use_id=%s", tool_use_id)

    async def action_quit(self) -> None:  # 退出时尝试将结果发给session，如果失败也不干预直接退出
        if self._client is not None and self._session_id is not None:
            try:
                await self._client.send_command("session.close", {"session_id": self._session_id})
            except (IpcError, RuntimeError, OSError):
                self._append(Static("[yellow]warning: failed to close session[/yellow]"))
        self.exit()

    async def _do_send_message(self, content: str) -> None:
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

    # 在 worker 中执行手动压缩命令，完成后显示结果横幅
    async def _do_compact(self) -> None:
        if self._client is None or self._session_id is None:
            return
        self._append(Static("[dim]⚡ compacting context...[/dim]", classes="log-line"))
        try:
            result = await self._client.send_command(
                "session.compact",
                {"session_id": self._session_id, "focus": ""},
            )
            summary_tokens = result.get("summary_tokens", 0)
            saved_tokens = result.get("saved_tokens", 0)
            self._last_context_pct = 0.0
            self._append(Static(
                f"[bold cyan]⚡ Context compacted[/bold cyan]"
                f"  [dim]summary={summary_tokens} tokens  saved≈{saved_tokens} tokens[/dim]",
                classes="log-line",
            ))
        except (IpcError, RuntimeError, OSError) as e:
            self._append(Static(f"[red]compact error: {e}[/red]", classes="log-line"))
    # 将输入框提交内容发送给当前 chat session
    async def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        # 你定义的事件类：ChatTextArea.Submitted要与名字对齐。
        content = event.value.strip()
        if not content:
            return
            # 检测 /compact 指令
        if content == "/compact":
            event.area.text = ""
            if self._client is None:
                self._append(Static("[yellow]compact: not connected[/yellow]", classes="log-line"))
            elif self._session_id is None:
                self._append(Static("[yellow]compact: no session[/yellow]", classes="log-line"))
            elif self._busy:
                self._append(Static("[yellow]compact: agent busy[/yellow]", classes="log-line"))
            else:
                self.run_worker(self._do_compact(), name="compact", exclusive=False)
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

            async def on_event(event: dict[str, Any]) -> None:  # 异步函数改成同步函数的路由了，因此额外套一层异步的皮
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
                        "permission.*",
                        "context.*",
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
                self._update_header("ready")  # 没走到这里
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

    # 将选择控件挂载到 Screen 顶层（#prompt 之前），避免 VerticalScroll 争抢焦点
    def _mount_permission_select(self, select: PermissionSelect) -> None:  # 把控件放在最前面。
        self.mount(select, before="#prompt")

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
        elif t == "log.line":
            level = event.get("level", "INFO")
            color = "bold red" if level == "ERROR" else ("yellow" if level == "WARNING" else "dim")
            self._append(Static(
                f"[{color}]{level}[/{color}]  "
                f"[dim]{event.get('source', '')}[/dim]  {event.get('message', '')}",
                classes="log-line",
            ))
        elif t == "permission.requested":
            tool_use_id = str(event.get("tool_use_id", ""))
            tool_name = str(event.get("tool_name", ""))
            param_preview = str(event.get("param_preview", ""))
            try:
                _focused_repr = repr(self.focused)
            except Exception:
                _focused_repr = "?"
            log.info(
                "permission.requested tool=%s id=%s  app.focused=%s",
                tool_name, tool_use_id, _focused_repr,
            )
            perm_block = PermissionBlock(tool_use_id, tool_name, param_preview)
            self._pending_permission_blocks[tool_use_id] = perm_block
            prompt = self._prompt()
            if prompt is not None:
                prompt.disabled = True
                prompt.border_title = "permission required"
            self._append(perm_block)
            select = PermissionSelect(tool_use_id)
            self._mount_permission_select(select)
            log.debug("PermissionSelect mounted before #prompt  pending=%d", len(self._pending_permission_blocks))
        elif t == "permission.denied":
            # 处理超时或断连等非用户交互触发的 deny（用户主动 deny 已由 on_permission_select_decided 处理）
            tool_use_id = str(event.get("tool_use_id", ""))
            decision = str(event.get("decision", "denied"))
            if tool_use_id in self._pending_permission_blocks:
                perm_block = self._pending_permission_blocks.pop(tool_use_id, None)
                if perm_block is not None:
                    perm_block._resolve(decision)
                try:
                    select = self.query_one(PermissionSelect)
                    select.remove()
                except Exception:
                    pass
                if not self._pending_permission_blocks:
                    p = self._prompt()
                    if p is not None:
                        p.disabled = False
                        p.read_only = False
                        p.border_title = "type a message — enter to send, ⌘/⇧/⌥+enter for newline"
                        p.focus()
        elif t == "llm.usage":
            pct = float(event.get("context_pct") or 0.0)
            self._last_context_pct = pct
            ctx_bar = self._render_ctx_bar(pct)
            self._append(Static(
                f"tokens in={event.get('input_tokens')} "
                f"out={event.get('output_tokens')} "
                f"cache={event.get('cache_read_input_tokens')} "
                f"{ctx_bar}",
                classes="usage",
            ))
        elif t == "context.compacted":
            orig = event.get("original_tokens", 0)
            summary = event.get("summary_tokens", 0)
            self._last_context_pct = 0.0
            self._append(Static(
                f"Context compacted original≈{orig} tokens → summary={summary} tokens",
                classes="log-line",
            ))

    def _break_llm(self) -> None:
        if self._current_llm is not None:
            self._current_llm.finalize_markdown()
        self._current_llm = None

    def _append(self, widget: Widget) -> None:
        log_view = self.query_one("#log-view", VerticalScroll)
        log_view.mount(widget)
        log_view.scroll_end(animate=False)

    def _update_header(self, state: str) -> None:
        try:  # 确认header是否存在，如不存在直接返回
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
