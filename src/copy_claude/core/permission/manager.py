from __future__ import annotations

import re
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, UTC

from copy_claude.core.permission.policy import (
    evaluate,
    PermissionDecision,
    DEFAULT_POLICIES,
    ToolPolicy,
    matches_outside_cwd,
    param_preview
)
from copy_claude.core.permission.storage import load_policy_file, save_policy_file
from pathlib import Path
from dataclasses import dataclass
import asyncio

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(UTC).isoformat()


@dataclass
class _PendingRequest:  #
    future: asyncio.Future[str]
    session_id: str
    tool_name: str


# 静态规则如果返回 ALLOW 或 DENY，PermissionManager 可以直接返回。但如果结果是 ASK，就要暂停当前工具调用，等用户回应。
class PermissionManager:  # 根据工具对应的策略判断返回拒绝还是接受还是向用户询问。需要polices，tool_name
    def __init__(self,
                 policies: dict[str, ToolPolicy] | None = None,
                 *,
                 policy_file: Path | None = None,
                 timeout_s: float = 60.0, ):
        self._policies = policies or dict(DEFAULT_POLICIES)  # 根据工具名称获取策略。
        self._pending: dict[str, _PendingRequest] = {}  # 标记正在等待用户指示的工具调用请求。
        # (session_id, tool_name) → "allow" | "deny"（session 内存，重启丢失）
        self._session_always: dict[tuple[str, str], str] = {}  # 缓存的当前会话当前工具权限。用户选择允许一次或拒绝一次时临时缓存
        # tool_name → "allow" | "deny"（持久化，从 policy_file 加载）
        self._policy_file = policy_file  # 从磁盘加载的持久化权限管理。
        self._persistent_always: dict[str, str] = (
            load_policy_file(policy_file) if policy_file is not None else {}  # 用户选择总是拒绝或总是同意时加载进入policy_file
        )
        # 0 表示不超时
        self._timeout_s = timeout_s

    def evaluate(self, tool_name: str, params: dict[str, any]) -> PermissionDecision:
        return evaluate(tool_name, params, self._policies.get(tool_name))

    async def check_and_wait(self,  # 查询输入的工具名称，判断该拒绝、同意还是询问，询问时等待，超时拒绝。
                             tool_name: str,
                             tool_use_id: str,
                             params: dict[str, any],
                             session_id: str,
                             event_emitter: Callable[[dict[str, any]], Awaitable[None]], ) -> tuple[bool, str]:
        command = str(params.get("command","")) if tool_name == "bash" else ""  # command用于检查bash命令
        # command只有在bash命令才有具体str。
        policy = self._policies.get(tool_name)

        if command and policy:  # bash做特殊检查：在进行常规检查之前，先进行bash的特殊检查
            for pat in policy.deny_patterns:
                if re.search(pat, command):
                    logger.debug("permission: deny_pattern hit tool=%s", tool_name)
                    return False, "auto_deny"

        outside_cwd = bool(command and matches_outside_cwd(command))
        if not outside_cwd:  # 此分支为非bash或bash命令但没越界的情况
            session_key = (session_id, tool_name)
            if session_key in self._session_always:  # 检查临时情况
                decision = self._session_always[session_key]
                logger.debug("permission: session_always hit tool=%s,decision is %s", tool_name, decision)
                return decision == "allow", f"auto_{decision}"

            if tool_name in self._persistent_always:
                decision = self._persistent_always[tool_name]
                logger.debug("permission: persistent always hit tool=%s,decision is %s", tool_name, decision)
                return decision == "allow", f"auto_{decision}"
            if command and policy:
                for pat in policy.allow_patterns:
                    if re.search(pat, command):
                        return True, "auto_allow"

            if policy is not None:
                if policy.default == PermissionDecision.ALLOW:
                    return True, "auto_allow"
                if policy.default == PermissionDecision.DENY:
                    return False, "auto_deny"
            # default == ASK（bash、unknown tool）→ fall through to Future
        # 从上述分支未能返回意味着policy是PermissionDecision.ASK
        loop = asyncio.get_event_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[tool_use_id] = _PendingRequest(future, session_id, tool_name)
        await event_emitter(  # 发送事件等待回复如果允许就返回allow，否则返回deny。
            {
                "type": "permission.requested",
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "params": params,
                "param_preview": param_preview(tool_name, params),
                "session_id": session_id,
                "ts": _now(),
            }
        )
        try:
            if self._timeout_s > 0:
                raw = await asyncio.wait_for(future, timeout=self._timeout_s)
                # raw是用户端Set_result的结果应该是allow_one\allow_always\deny_once\deny_always之类的。
            else:
                raw = await future
        except asyncio.TimeoutError:
            self._pending.pop(tool_use_id, None)
            logger.info("permission: timeout tool_use_id=%s tool=%s", tool_use_id, tool_name)
            return False, "timeout"

        allowed = self._apply_response(raw, session_id, tool_name)
        return allowed, raw

    def _apply_response(self, decision: str, session_id: str, tool_name: str) -> bool:
        allow = decision in ("allow_once", "always_allow")
        if decision == "always_allow":  # 保存在permission_
            self._session_always[(session_id, tool_name)] = "allow"
            self._persistent_always[tool_name] = "allow"
            logger.info(
                "permission: always allow tool=%s policy_file=%s persistent=%s",
                tool_name, self._policy_file, self._persistent_always,
            )
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    logger.exception("permission: failed to write policy.toml path=%s", self._policy_file)
            else:
                logger.warning("permission: policy_file is None, skipping persistence")
        elif decision == "always_deny":
            self._session_always[(session_id, tool_name)] = "deny"
            self._persistent_always[tool_name] = "deny"
            logger.info(
                "permission: always deny tool=%s policy_file=%s persistent=%s",
                tool_name, self._policy_file, self._persistent_always,
            )
            if self._policy_file is not None:
                try:
                    save_policy_file(self._persistent_always, self._policy_file)
                    logger.info("permission: policy.toml written path=%s", self._policy_file)
                except Exception:
                    logger.exception("permission: failed to write policy.toml path=%s", self._policy_file)
            else:
                logger.warning("permission: policy_file is None, skipping persistence")
        return allow

    def respond(self, tool_use_id: str, decision: str) -> None:  # 根据工具id和权限确定req。
        req = self._pending.pop(tool_use_id, None)
        if req is None:
            logger.warning("permission.respond: unknown tool_use_id=%s", tool_use_id)
            return
        if not req.future.done():
            req.future.set_result(decision)
