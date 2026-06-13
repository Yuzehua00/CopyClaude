from __future__ import annotations
from copy_claude.core.tools.base import BaseTool,ToolResult
from pydantic import BaseModel, ConfigDict
_MAX_BYTES = 512 * 1024   # 512 KB
from pathlib import Path


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str

class ReadFileTool(BaseTool):
    params_model = ReadFileParams
    name = "read_file"
    description = (
        "Read the text content of a file. "
        "Path must be relative to the current working directory. "
        "Files larger than 512 KB are truncated."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            }
        },
        "required": ["path"],
    }
    async def invoke(self,params: dict[str, object])->ToolResult:
        path_str = str(params["path"])
        if ".." in Path(path_str).parts: # 路径不能包含 ..（防止读取上层目录之外的文件）
            raise PermissionError(f"路径不能包含..，请检查： {path_str}")
        # 这个工具抛出 PermissionError 后会被 invoke_tool() 的 except Exception 捕获并转成错误结果，流程照常继续。

        raw = Path(path_str).read_bytes() # 读为一行
        truncated = len(raw) > _MAX_BYTES # 如果超出最大长度
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += "\n[truncated]"
        return ToolResult(content=text,is_error=False)