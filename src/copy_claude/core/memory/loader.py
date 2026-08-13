# 负责加载记忆，记忆分为三种类型：
# 1.存放在C盘的~/.copyclaude/context.md
# 2.存在项目的.copyclaude/context.md
# 3.ai调用note_save工具保存的notes.md
from __future__ import annotations
from pathlib import Path


def load_context_file(path: Path) -> str: # 加载指定路径的md返回读取的文本。
    p = path.expanduser()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()
