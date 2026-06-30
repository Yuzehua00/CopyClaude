from __future__ import annotations
from dataclasses import dataclass,field
from typing import Literal

SessionMode = Literal["chat","one_shot"] # 会话模式分为多轮对话（对应chat命令）和一次对话（对应run命令）
SessionStatus = Literal["active","waiting_for_input","closed"] # 会话状态分为正在运行但不可输入、可输入，关闭三种状态。

@dataclass
class Session:
    id:str
    mode: SessionMode
    status: SessionStatus
    title:str
    created_at:str
    updated_at:str
    run_ids:list[str] = field(default_factory=list)

    # 将 Session 转为可写入 meta.json 的普通 dict
    def to_dict(self) -> dict:
        return {
            "id":self.id,
            "mode":self.mode,
            "status":self.status,
            "title":self.title,
            "created_at":self.created_at,
            "updated_at":self.updated_at,
            "run_ids":self.run_ids,
        }
    # 从 meta.json 的 dict 还原 Session 对象
    @classmethod
    def from_dict(cls,d:dict) -> Session:
        return cls(
            id=d["id"],
            mode=d["mode"],
            status=d["status"],
            title=d["title"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            run_ids=d["run_ids"],
        )
