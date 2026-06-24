from __future__ import annotations
from pathlib import Path
from datetime import datetime, UTC
from copy_claude.core.task.model import Task
from typing import List, Any
import json


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TaskManager:  # 任务存储格式是在task文件夹下的task_1.json\task_2.json
    def __init__(self, tasks_dir: Path) -> None:  # 初始化：确保目录存在，扫描现有文件确定下一个 ID
        self._dir = tasks_dir  # 任务保存路径
        self._dir.mkdir(exist_ok=True)
        self._next_id = self._max_id()

    def _max_id(self) -> int:  # 读取目录下的task_*.json文件获取最大编号
        ids = [
            int(f.stem.split("_")[1])
            for f in self._dir.glob("task_*.json")  # 遍历文件夹下所有符合task_*.json格式同时*是数字的文件，得到编号。
            if f.stem.split("_")[1].isdigit()
        ]
        return max(ids) if ids else 0

    def _load(self, task_id: int) -> Task:
        path = self._dir / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"task {task_id} not found")
        return Task.from_dict(json.loads(path.read_text()))

    def _save(self, task: Task) -> None:  # 将输入的任务保存到指定编号的文档json中
        path = self._dir / f"task_{task.id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False))

    # 创建新任务，写入 JSON 文件，返回 Task
    def create(self,
               subject: str,
               description: str,
               *,
               blocked_by: List[Any] | None = None) -> Task:  # id来源next_id，其余的信息应该输入。
        now = _now()
        task = Task(id=self._next_id,
                    subject=subject,
                    status="pending",
                    description=description,
                    blocked_by=blocked_by,
                    created_at=now,
                    updated_at=now)
        self._next_id += 1
        self._save(task)
        return task

    # 读取指定编号的任务
    def get(self, task_id: int) -> Task:
        return self._load(task_id)

    # 更新任务状态或依赖列表；status="completed" 时自动清理其他任务的 blocked_by
    def update(self,
               task_id: int,
               status: str,
               *,
               add_blocked_list: list[any] | None = None,
               remove_blocked_list: list[any] | None = None) -> Task:
        task = self._load(task_id)
        task.status = status
        if status is not None:
            if status not in ["pending", "in_progress", "completed"]:
                raise ValueError(f"invalid status: {status}")
            if task.status == "completed":
                self._clean_dependency(task_id)

        if add_blocked_list is not None:
            task.blocked_by = list(set(add_blocked_list + task.blocked_by))
        if remove_blocked_list is not None:
            task.blocked_by = [x for x in task.blocked_by if x not in remove_blocked_list]
        task.updated_at=_now()
        self._save(task)
        return task

    # 返回所有任务，按 ID 升序排列
    def list_all(self) -> List[Task]:
        tasks = []
        for f in sorted(self._dir.glob("task_*.json"), key=lambda p: int(p.stem.split("_")[1])):
            try:
                tasks.append(Task.from_dict(json.loads(f.read_text())))
            except (ValueError, KeyError):
                pass
        return tasks

    # 将 completed_id 从所有其他任务的 blocked_by 列表中移除
    def _clean_dependency(self, completed_id: int) -> None:  # 为什么不用list_all，理由在于不用排序浪费时间。
        for f in self._dir.glob("task_*.json"):
            try:  # 这里有一个检查是否文件能打开。
                data = json.loads(f.read_text())
            except (ValueError, json.JSONDecodeError):
                continue
            blocked = [int(x) for x in data.get("blocked_by", [])]
            if completed_id in blocked:
                data["blocked_by"] = [x for x in blocked if x != completed_id]
                data["updated_at"] = _now()
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False))

        # 格式化任务列表摘要，供 task_list 工具返回给 Agent
    def format_list(self) -> str:
        tasks = self.list_all()
        if not tasks:
            return "No tasks."
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        lines = []
        for t in tasks:
            blocked = f" (blocked by: {t.blocked_by})" if t.blocked_by else ""
            lines.append(f"{marker.get(t.status, '[?]')} #{t.id}: {t.subject}{blocked}")
        return "\n".join(lines)