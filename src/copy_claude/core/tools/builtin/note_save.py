from __future__ import annotations

from copy_claude.core.tools.base import BaseTool, ToolResult

from copy_claude.core.session.store import SessionStore


class NoteSaveTool(BaseTool):
    name = 'note_save'
    description = (
        "Save a fact or decision to the session's notes. "
        "These notes will be visible to you in future turns of this session."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
        },
        "required": ["content"],
    }

    def __init__(self, store: SessionStore, sid: str, run_id: str):
        self._store = store
        self._sid = sid
        self._run_id = run_id

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        content = str(params["content"])
        if content == "":
            return ToolResult("content is empty", True)
        self._store.append_note(self._sid, content, self._run_id)
        return ToolResult("note save success")
