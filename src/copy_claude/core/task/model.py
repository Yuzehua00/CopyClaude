from __future__ import annotations
from dataclasses import dataclass
from typing import Any,Literal

TaskStatus = Literal["pending", "in_progress", "completed"]

@dataclass
class Task:
    id: int # TaskManager生成
    subject: str # 外界输入
    description: str # 外界输入
    status: TaskStatus # 外界输入
    blocked_by: list[int] # 任务依赖关系用 blocked_by 字段表示：task_2.blocked_by = [1] 意味着任务 2 在等待任务 1 完成才能开始。
    created_at: str # 创建时间，内部生成
    updated_at: str # 上传时间，内部生产

    # 序列化为 dict，字段名与 JSON 文件格式一致
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "blocked_by": self.blocked_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # 从 dict 构造 Task
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=int(data["id"]),
            subject=str(data["subject"]),
            description=str(data.get("description", "")),
            status=data.get("status", "pending"),
            blocked_by=[int(x) for x in data.get("blocked_by", [])],
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )

