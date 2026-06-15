from __future__ import annotations
import asyncio
from typing import List
import uuid
from pydantic import BaseModel
from dataclasses import dataclass
from copy_claude.core.trace.writer import TraceWriter
from copy_claude.core.bus.envelope import EventPushEnvelope
import fnmatch

@dataclass
class _Subscription:
    sub_id: str
    writer: asyncio.StreamWriter
    topics: List[str]
    scope: str


class IpcEventBroadcaster:  # 事件广播，基本上跟EventBus应该差不多，
    def __init__(self, trace: TraceWriter | None = None) -> None:
        self._subscriptions: list[_Subscription] = []
        self._trace = trace

    async def handle(self, event: BaseModel) -> None: # 检查所有的Subscription，根据事件类型发给感兴趣的订阅者。
        # 检查event的type然后看sub里的topics是否匹配的上。
        event_dict = event.model_dump()
        event_type = event_dict.get("type","")
        run_id = event_dict.get("run_id")

        dead: list[asyncio.StreamWriter] = [] # 已断开连接的写程序。需要定时清理

        for sub in self._subscriptions:
            if not self._matches_scope(run_id=run_id,scope=sub.scope):
                continue
            if not self._matches_topic(event_type = event_type, topics= sub.topics):
                continue
            # 程序执行到此说明匹配成功。
            try:
                envelope = EventPushEnvelope(event=event_dict)
                sub.writer.write(envelope.model_dump_json().encode() + b"\n")
                await sub.writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                dead.append(sub.writer)  # 先记下，fan-out 完再清理

        for writer in dead:
            self.unsubscribe(writer)

    @staticmethod
    def _matches_scope(run_id: str | None, scope: str) -> bool: # 匹配subscription中的scope
        if scope == "global":
            return True
        if scope.startswith("run:"):
            return run_id == scope[4:]
        return False

    @staticmethod
    def _matches_topic(event_type: str, topics: list[str]) -> bool: # 匹配subscription中的topic
        return any(fnmatch.fnmatch(event_type, pattern) for pattern in topics)
    def subscribe(self, writer: asyncio.StreamWriter,
                  topics: List[str],
                  scope: str) -> str:
        sub_id = f"sub-{uuid.uuid4().hex[:8]}"
        subscription = _Subscription(sub_id, writer, topics, scope)
        self._subscriptions.append(subscription)
        return sub_id
    def unsubscribe(self, writer: asyncio.StreamWriter) -> None:
        self._subscriptions = [s for s in self._subscriptions if s.writer is not writer]