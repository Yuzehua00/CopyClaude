# 工具基类
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pydantic import BaseModel
from typing import ClassVar


@dataclass
class ToolResult:
    content: str  # 工具具体结果
    is_error: bool = False  # 是否报错
    # "runtime_error" | "timeout" | "schema_error" | "permission_denied"
    error_type: str | None = None


class BaseTool(ABC):
    name: str  # 工具名称
    description: str  # 工具怎么用
    input_schema: dict[str, object]  # 输入参数
    params_model: ClassVar[type[BaseModel] | None] = None

    # 执行工具调用，返回工作结果。
    @abstractmethod
    async def invoke(self, params: dict[str, object]) -> ToolResult: ...
