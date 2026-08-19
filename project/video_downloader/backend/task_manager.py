"""内存态任务存储 + 状态机流转 (线程安全, ADR-0003).

任务状态机 (见 CONTEXT.md):
    pending → resolving → resolved → queued → downloading → completed
                              ↘                ↘
                               failed            failed
    completed/failed → expired
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import downloader

# 任务状态常量 (领域状态机)
STATUS_PENDING = "pending"
STATUS_RESOLVING = "resolving"
STATUS_RESOLVED = "resolved"
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"


@dataclass
class Task:
    id: int
    url: str
    kind: str  # "resolve" | "download"
    status: str = STATUS_PENDING
    title: str | None = None
    cover: str | None = None
    duration: float | None = None
    site: str | None = None
    formats: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class TaskManager:
    """任务存储与状态更新; 所有操作持有 RLock 保证线程安全."""

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._lock = threading.RLock()
        self._seq = 0

    def create_task(self, url: str, kind: str) -> Task:
        with self._lock:
            self._seq += 1
            task = Task(id=self._seq, url=url, kind=kind)
            self._tasks[task.id] = task
            return task

    def update_status(self, task_id: int, status: str, **fields: Any) -> Task:
        with self._lock:
            task = self._tasks[task_id]
            task.status = status
            for key, value in fields.items():
                setattr(task, key, value)
            task.updated_at = time.time()
            return task

    def get_task(self, task_id: int) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[Task]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.id, reverse=True)
            return tasks[:limit]

    def resolve(self, url: str) -> Task:
        """同步解析链接: 任务状态 pending → resolving → resolved / failed.

        解析耗时通常在秒级, 同步等待引擎返回; 失败时任务标记 failed 并抛出
        ResolveError, 由路由层转为 4xx 响应.
        """
        task = self.create_task(url=url, kind="resolve")
        self.update_status(task.id, STATUS_RESOLVING)
        try:
            info = downloader.resolve(url)
        except downloader.ResolveError as e:
            self.update_status(task.id, STATUS_FAILED, error=str(e))
            raise
        self.update_status(
            task.id,
            STATUS_RESOLVED,
            title=info["title"],
            cover=info.get("cover"),
            duration=info.get("duration"),
            site=info.get("site"),
            formats=info.get("formats", []),
        )
        return self.get_task(task.id)


# 模块级单例: 路由与测试共享同一任务存储
manager = TaskManager()
