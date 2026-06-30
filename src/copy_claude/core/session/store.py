from __future__ import annotations

import logging

from copy_claude.core.session.model import Session
from datetime import datetime,UTC
import json
from pathlib import Path

logger = logging.getLogger(__name__)

def _now() -> str:
    return datetime.now(UTC).isoformat()
class SessionStore:
    def __init__(self,root:Path)->None:
        self.root=root.expanduser()
        self.root.mkdir(parents=True,exist_ok=True)

    # 返回指定 session 的目录路径
    def session_dir(self,sid:str)->Path:
        return self.root/sid

        # 返回指定 session 下的 runs 目录路径
    def runs_dir(self, sid: str) -> Path:
        return self.session_dir(sid) / "runs"

    def write_meta(self,session:Session)->None:
        path = self.session_dir(session.id)
        path.mkdir(parents=True,exist_ok=True) # 会话根目录
        (path / "meta.json").write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_meta(self,sid:str)->Session:
        path = self.session_dir(sid)/"meta.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session.from_dict(data)

    def append_message(self,
                       sid:str,
                       role:str,
                       content:str,
                       run_id:str|None=None,)->None:
        row:dict[str,any] = {"ts":_now(),"role":role,"content":content}
        if run_id is not None:
            row["run_id"] = run_id
        path = self.session_dir(sid)
        path.mkdir(parents=True, exist_ok=True)
        with (path / "thread.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
# 批量追加一次 run 新产生的消息到 thread.jsonl
    def append_messages(
        self,
        sid: str,
        messages: list[dict[str, any]],
        run_id: str,
    ) -> None:
        for msg in messages:
            self.append_message(
                sid,
                role=str(msg["role"]),
                content=msg["content"],
                run_id=run_id,
            )

    def read_messages(self,sid: str) -> list[dict[str, any]]:
        path = self.session_dir(sid)/ "thread.jsonl"
        if not path.exists():
            return []
        messages: list[dict[str, any]] = []
        for line_no,line in enumerate(path.read_text(encoding="utf-8").splitlines(),start=1):
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("跳过未保存完整上下文的行 sid=%s line=%s", sid, line_no)
                continue
            role = msg.get("role")
            if role not in ("user", "assistant"):
                logger.warning(
                    "跳过未知role的行 sid=%s line=%s role=%s",
                    sid,
                    line_no,
                    role,
                )
                continue
            messages.append({"role": role, "content": msg.get("content", "")})
        messages = self._trim_orphan_tool_use(messages)
        return messages # 有Anthropic报错需要重做。

    # 裁掉尾部未配对 tool_use 以及其后的消息，避免 Anthropic messages.invalid
    def _trim_orphan_tool_use(self,messages:list[dict[str, any]])->list[dict[str, any]]:
        # 通过集合的方式配对，在message里assistant对应工具调用的id添加，user里对应工具结果的id删除。
        pending:set[str]=set()
        last_balanced = 0
        for idx,msg in enumerate(messages,start=1):
            content = msg.get("content")
            if isinstance(content,list):
                for block in content:
                    role = block.get("role")
                    if role == "assistant":
                        if block.get("type") == "tool_use":
                            pending.add(str(block.get("id","")))
                    else:
                        if block.get("type") == "tool_result":
                            pending.discard(str(block.get("tool_use_id","")))
            if not pending:
                last_balanced = idx
        if pending: # 遍历结束后如果集合未清干净，就需要截断输出，以免不满足Anthropic关于上下文的规定
            logger.warning("从会话中截断未保存工具结果的内容。")
            return messages[:last_balanced]
        return messages

# 将压缩后的消息对覆盖写入 thread.jsonl，原文件备份为 thread_<ts>.jsonl.bak
    def compacted(self,sid:str,messages:list[dict[str, any]])->None: # messages对应压缩后的信息。
        path = self.session_dir(sid)/"thread.jsonl"
        ts_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        bak = self.session_dir(sid) / f"thread_{ts_str}.jsonl.bak"
        if path.exists():
            path.rename(bak)
        with path.open("w", encoding="utf-8") as f:
            for msg in messages:
                row: dict[str, any] = {"ts": _now(), "role": msg["role"], "content": msg["content"]}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 读取 notes.md 全文，文件不存在时返回空字符串
    def read_notes(self, sid: str) -> str:
        path = self.session_dir(sid) / "notes.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # 将一条主动笔记追加到 notes.md
    def append_note(self, sid: str, content: str, run_id: str) -> None:
        path = self.session_dir(sid)
        path.mkdir(parents=True, exist_ok=True)
        with (path / "notes.md").open("a", encoding="utf-8") as f:
            f.write(f"## Note ({_now()}, {run_id})\n{content}\n\n")