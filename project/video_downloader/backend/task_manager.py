"""内存态任务存储 + 状态机流转 + 并发调度 (线程安全, ADR-0003).

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

from . import config, downloader

# 任务状态常量 (领域状态机)
STATUS_PENDING = "pending"
STATUS_RESOLVING = "resolving"
STATUS_RESOLVED = "resolved"
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"

# 并发下载槽位 (免费档 1, 会员档 3 由 T05 接入)
CONCURRENCY = 1

# 调度器扫描间隔 (秒): 把 queued 任务分配到空闲槽位 (PRD 设计值约 0.5s)
_SCHEDULER_INTERVAL = 0.5


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
    format_id: str | None = None
    progress: float = 0.0
    message: str | None = None
    file_path: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class TaskManager:
    """任务存储、状态更新与下载调度; 所有操作持有 RLock 保证线程安全."""

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._lock = threading.RLock()
        self._seq = 0
        self._active = 0  # 当前执行中的下载任务数 (并发槽占用)
        self._scheduler: threading.Thread | None = None
        self._scheduler_stop = threading.Event()

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
        self._fill_resolved(task)
        return self.get_task(task.id)

    def _fill_resolved(self, task: Task) -> dict[str, Any]:
        """执行解析并回填元信息 (resolve / create_download 共用解析段)."""
        self.update_status(task.id, STATUS_RESOLVING)
        try:
            info = downloader.resolve(task.url)
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
        return info

    def create_download(self, url: str, format_id: str) -> Task:
        """创建下载任务: 解析 → 校验档位 → 入队, 启动调度器.

        解析失败或档位无效时任务标记 failed 并抛出异常 (ResolveError / ValueError),
        由路由层转为 4xx 响应.
        """
        task = self.create_task(url=url, kind="download")
        info = self._fill_resolved(task)
        if not any(f["format_id"] == format_id for f in info.get("formats", [])):
            self.update_status(task.id, STATUS_FAILED, error=f"无效档位: {format_id}")
            raise ValueError(f"无效档位: {format_id}")
        self.update_status(task.id, STATUS_QUEUED, format_id=format_id)
        self.ensure_scheduler()
        return self.get_task(task.id)

    def ensure_scheduler(self) -> None:
        """确保后台调度线程运行 (daemon, 首次入队时惰性启动)."""
        with self._lock:
            if self._scheduler is not None and self._scheduler.is_alive():
                return
            self._scheduler_stop.clear()
            self._scheduler = threading.Thread(
                target=self._scheduler_loop, name="download-scheduler", daemon=True
            )
            self._scheduler.start()

    def stop_scheduler(self) -> None:
        """停止调度线程 (服务退出时调用, 测试中无需显式停止).

        注意: 不 join, 已在执行的 worker 继续跑完 (daemon, 进程退出即终止);
        停止事件置位后若线程仍存活, ensure_scheduler 不会重启 (服务即将退出).
        """
        self._scheduler_stop.set()

    def _scheduler_loop(self) -> None:
        """周期扫描: 有空闲并发槽时, 把排队任务派发给执行线程."""
        while not self._scheduler_stop.is_set():
            task = None
            with self._lock:
                if self._active < CONCURRENCY:
                    task = self._next_queued()
                    if task:
                        self._active += 1
                        self.update_status(task.id, STATUS_DOWNLOADING)
            if task:
                worker = threading.Thread(
                    target=self._run_download,
                    args=(task.id,),
                    name=f"download-worker-{task.id}",
                    daemon=True,
                )
                worker.start()
            else:
                self._scheduler_stop.wait(_SCHEDULER_INTERVAL)

    def _next_queued(self) -> Task | None:
        """返回队列中最早的排队任务 (FIFO, 按任务 id 升序)."""
        for t in sorted(self._tasks.values(), key=lambda t: t.id):
            if t.status == STATUS_QUEUED:
                return t
        return None

    def _run_download(self, task_id: int) -> None:
        """执行下载并更新任务状态 (独立线程, 完成后释放并发槽)."""
        try:
            task = self.get_task(task_id)
            if task is None:  # 理论上不可达 (worker 仅处理入队任务), 防御式处理
                raise RuntimeError(f"task {task_id} not found")
            path = downloader.download(
                task.url,
                task.format_id,
                config.DOWNLOADS_DIR,
                self._progress_hook(task_id),
            )
            self.update_status(
                task_id,
                STATUS_COMPLETED,
                file_path=path,
                progress=100.0,
                message="下载完成",
            )
        except downloader.DownloadError as e:
            self.update_status(task_id, STATUS_FAILED, error=str(e))
        except Exception as e:  # 引擎外异常 (磁盘/中断等): 标记失败而非悬挂
            self.update_status(task_id, STATUS_FAILED, error=f"下载异常: {e}")
        finally:
            with self._lock:
                self._active -= 1

    def _progress_hook(self, task_id: int):
        """yt-dlp 进度回调: 转发到任务 progress / message (上限 99, 完成时置 100)."""

        def hook(d: dict[str, Any]) -> None:
            if d.get("status") != "downloading":
                return
            # 未知总量时引擎提供 total_bytes_estimate, 兜底避免进度恒为 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            percent = (done / total * 100) if total else 0.0
            self.update_status(
                task_id,
                STATUS_DOWNLOADING,
                progress=min(percent, 99.0),
                message=f"下载中 {percent:.0f}%",
            )

        return hook


# 模块级单例: 路由与测试共享同一任务存储
manager = TaskManager()
