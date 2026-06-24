from __future__ import annotations
from dataclasses import dataclass,field
from typing import List,Any

@dataclass
class ExecutionContext: # Agent记忆模块。聊天上下文,每次交互的时候都调用。
    run_id: str
    goal: str
    max_steps: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    # messages 是整个上下文最核心的部分。它的格式和 Anthropic API 要求的完全一致，涉及对话记录、思维链记录、工具调用记录
    step: int = 0 # 当前步数
    status: str = "running"  # "running" | "success" | "failed" ，决定是否运行
    reason: str | None = None
    result: str = ""

    def __post_init__(self) -> None:
        # goal 在初始化时自动变成第一条对话消息
        if not self.messages:
            self.messages.append({"role": "user", "content": self.goal})

    def add_assistant_message(self, content: list[Any]) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_result(self, tool_use_id: str, content: str, is_error: bool = False) -> None:
        # 工具结果作为 user 消息追加，只要是消息就必须存在messages里
        # add_tool_result 里有一条 Anthropic 的格式要求：同一步骤里的多个工具调用结果，必须合并在同一条 user 消息里。
        block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if is_error:
            block["is_error"] = True

        last = self.messages[-1] if len(self.messages) > 0 else None # 获取最后一条消息。
        if (last # 最后一条消息存在
            and last["role"]=="user" # 最后一条消息为user消息，对应工具结果为user消息追加。
            and isinstance(last["content"], list) # 最后一条消息的content是列表
            and all(b.get("type") == "tool_result" for b in last["content"])): # 最后一条消息的content中所有信息都是工具结果
            last["content"].append(block)
        else:
            self.messages.append({"role": "user", "content": [block]})

    def is_done(self) -> bool:
        return self.status != "running"

    def mark_success(self) -> None:
        self.status = "success"
    def mark_failed(self, reason: str) -> None:
        self.status = "failed"
        self.reason = reason