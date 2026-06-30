from __future__ import annotations

import logging
from datetime import datetime, UTC
import uuid
from typing import Callable
import asyncio

from copy_claude.core.runs import new_run_id
from copy_claude.core.session.model import Session, SessionMode, SessionStatus
from copy_claude.core.session.store import SessionStore
from copy_claude.core.bus.envelope import HandlerError
from copy_claude.core.bus.events import (
    SessionCreatedEvent,
    SessionResumedEvent,
    SessionReceivedMessageEvent,
    SessionWaitingForInputEvent,
    SessionClosedEvent)
from copy_claude.core.events.bus import EventBus
from copy_claude.core.llm.base import LLMProvider
from copy_claude.core.runner import AgentRunner

SESSION_NOT_FOUND = -32010
SESSION_CLOSED = -32011
SESSION_BUSY = -32012

log = logging.getLogger(__name__)
def _now():
    return datetime.now(UTC).isoformat()


class SessionManager:
    # 初始化会话管理器，接入文件存储、runner 工厂、事件总线和可选的 LLM provider（用于手动压缩）
    def __init__(self,
                 store: SessionStore,
                 runner_factory: Callable[[], AgentRunner],
                 bus: EventBus,
                 provider: LLMProvider,
                 ):
        self._store = store  # 用于将某些内容写入meta.json
        self._bus = bus  # 会话的事件也要广播器，需要事件总线。
        self._runner_factory = runner_factory  # 是AgentRunner，多轮对话要调用一轮对话的run_and_capture
        self._provider = provider  # 用于压缩完整上下文事件。
        self._sessions: dict[str, Session] = {}  # 根据sid查询具体的会话。
        self._locks: dict[str, asyncio.Lock] = {}  # 给会话上锁，视为一种互斥资源，不能多次访问。
        self._skill_loader = None  # 加载已经下载的技能。

    async def create(self, mode: SessionMode, title: str = "") -> Session:
        s_id = f"sess-{uuid.uuid4().hex[:12]}"
        ts = _now()
        session = Session(
            id=s_id,
            mode=mode,
            status="active",
            title=title,
            created_at=ts,
            updated_at=ts,
            run_ids=[]
        )
        self._sessions[s_id] = session
        self._locks[s_id] = asyncio.Lock()  # 在创建会话时创建锁。
        self._store.write_meta(session)
        await self._bus.publish(SessionCreatedEvent(
            session_id=session.id,
            mode=mode,
            ts=ts,
        ))
        return session

    def _get_session(self, sid: str) -> Session:
        session = self._sessions.get(sid)
        if session is None:
            raise HandlerError(SESSION_NOT_FOUND, "session not found")
        return session

    def get_history(self, sid: str) -> list[dict[str, any]]:
        self._get_session(sid)
        return self._store.read_messages(sid)

    async def close(self, sid: str) -> None:
        session = self._get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")
        async with lock:
            session.status = "closed"
            session.updated_at = _now()
            self._store.write_meta(session)
            await self._bus.publish(SessionClosedEvent(session_id=sid,ts=session.updated_at))

    async def send_message(self, sid: str, content: str, run_id: str | None = None) -> str:
        # 根据sid找到session
        session = self._get_session(sid)
        lock = self._locks[sid]
        if lock.locked():
            raise HandlerError(SESSION_BUSY, "session busy")
        async with lock:
            if session.status == "closed":
                raise HandlerError(SESSION_CLOSED, "session closed")
            if session.status == "waiting_for_input":
                await self._bus.publish(SessionResumedEvent(session_id=sid, ts=_now()))
            self._store.append_message(sid=sid, role="user", content=content, run_id=run_id)  # 将新内容写入jsonl
            await self._bus.publish(SessionReceivedMessageEvent(session_id=sid, ts=_now(), content=content))
            if not session.title:
                session.title = content[:40]

            run_id = run_id or new_run_id()
            session.run_ids.append(run_id)
            session.updated_at = _now()
            self._store.write_meta(session)
            runner = self._runner_factory()
            await runner.run_and_capture(goal=content, run_id=run_id, session=session, store=self._store)
            log.info(msg="SessionManager->send_message->run_and_capture Successful")
            session.updated_at = _now()
            log.info(msg="SessionManager->send_message->update time Successful")
            if session.mode == "one_shot":
                session.status = "closed"
                await self._bus.publish(SessionClosedEvent(session_id=sid, ts=session.updated_at))
                log.info(msg="SessionManager->send_message->bus publish one_shot Successful")
            else:
                session.status = "waiting_for_input"
                await self._bus.publish(
                    SessionWaitingForInputEvent(
                        session_id=sid,
                        last_run_id=run_id,
                        ts=session.updated_at,
                    )
                )
                log.info(msg="SessionManager->send_message->bus publish chat waiting for input Successful")
            self._store.write_meta(session)
            return run_id
