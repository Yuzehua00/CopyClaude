from __future__ import annotations

from pydantic import BaseModel,Discriminator # 通过Discriminator来访问所有command类的type字段直接确定command类型，加速性能。

from typing import Annotated, Any, Literal

class PingCommand(BaseModel):
    type: Literal["core.ping"] = "core.ping"
    client: str

class PongResult(BaseModel):
    server_version: str # 服务器版本
    uptime_ms: int # 上传时间
    received_at: str  # 收自端口号ISO 8601

class EventSubscribeCommand(BaseModel):
    type: Literal["event.subscribe"] = "event.subscribe"
    topics: list[str]  # fnmatch 模式，如 ["step.*", "tool.*"]
    scope: str = "global"  # "global" | "run:<run_id>"
    replay_from_run: str | None = None  # run_id，设置则先从 events.jsonl 回放历史再接实时流

class EventSubscribeResult(BaseModel):
    subscribe_id: str
    replay_count: int

class AgentRunCommand(BaseModel):
    type: Literal["agent.run"] = "agent.run"
    goal:str

class AgentRunResult(BaseModel):
    run_id: str

Command = Annotated[ # Annotated	附加元数据，不改变类型	仍视为原始类型	元数据可通过 __metadata__ 访问
    PingCommand|
    EventSubscribeCommand|
    AgentRunCommand, # 基本类型
    Discriminator("type"), # 元数据，任意 Python 对象，通常是一些描述约束或行为的对象，元数据能跟前面的原始数据一起被读取，用来进行一些判断。
]