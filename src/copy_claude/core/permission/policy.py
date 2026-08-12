from __future__ import annotations

from enum import StrEnum

from dataclasses import dataclass, field
import re


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ASK"


OUTSIDE_CWD_HEURISTICS = [
    r"(^|\s)/[^\s]",  # absolute path
    r"(^|\s)~",  # home path
    r"(^|\s)\.\.(/|$|\s)",  # parent traversal
    r"\$\{?HOME\b",
    r"\$\{?PWD\b",
    r"(^|\s|;|&&|\|\|)cd(\s|$)",
]


@dataclass
class ToolPolicy:
    default: PermissionDecision
    allow_patterns: list[str] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)


DEFAULT_POLICIES = {
    "bash": ToolPolicy(default=PermissionDecision.ASK),
    "write_file": ToolPolicy(default=PermissionDecision.ASK),
    "read_file": ToolPolicy(default=PermissionDecision.ALLOW),
    "list_dir": ToolPolicy(default=PermissionDecision.ALLOW),
    "note_save": ToolPolicy(default=PermissionDecision.ALLOW),
}
_UNKNOWN_POLICY_DECISION = PermissionDecision.ASK
_OUTSIDE_CWD_RE: list[re.Pattern[str]] = [re.compile(p) for p in OUTSIDE_CWD_HEURISTICS]

# bash 参数中展示用的关键字段映射
_PREVIEW_KEY: dict[str, str] = {
    "bash":       "command",
    "read_file":  "path",
    "write_file": "path",
    "list_dir":   "path",
    "note_save":  "content",
}
_PREVIEW_MAX = 60

def matches_outside_cwd(command: str) -> bool:
    return any(pat.search(command) for pat in _OUTSIDE_CWD_RE)

# 为权限审批事件生成人类可读的参数摘要
def param_preview(tool_name:str,params:dict[str,any])->str:
    key_command = _PREVIEW_KEY.get(tool_name,None)
    if key_command and key_command in params:
        val = params[key_command]
        if len(val) > _PREVIEW_MAX:
            val = val[:_PREVIEW_MAX]
        return f"{key_command}={val}"
    snippet = str(params)
    return snippet[:_PREVIEW_MAX] if len(snippet) > _PREVIEW_MAX else snippet

def evaluate(tool_name: str, params: dict[str, any], policy: ToolPolicy | None = None) -> PermissionDecision:
    if policy is None:
        policy = DEFAULT_POLICIES.get(tool_name, None)
    if policy is None:
        return _UNKNOWN_POLICY_DECISION
    command = str(params.get("command", "")) if tool_name == "bash" else ""
    if command:
        for pat in policy.deny_patterns:
            if re.search(pat, command):
                return PermissionDecision.DENY

    if command and matches_outside_cwd(command):
        return PermissionDecision.ASK

    if command:
        for pat in policy.allow_patterns:
            if re.search(pat, command):
                return PermissionDecision.ALLOW

    return policy.default
