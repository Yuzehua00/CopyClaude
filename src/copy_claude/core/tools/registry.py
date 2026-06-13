from __future__ import annotations
from copy_claude.core.tools.base import BaseTool

class ToolRegistry(object): # 工具箱类，要有工具说明，和注册函数等内容
    def __init__(self)->None:
        self._tools:{str,BaseTool}={}

    # 同名覆盖
    def register(self, tool:BaseTool)->None:
        self._tools[tool.name] = tool

    def get(self, name:str)->BaseTool|None:
        return self._tools.get(name)

    # 返回所有工具的 Anthropic 格式 schema 列表
    def tool_schemas(self)->list[dict[str,object]]:
        return [
            {
                "name":tool.name,
                "description":tool.description,
                "input_schema":tool.input_schema,
            }
            for tool in self._tools.values()
        ]