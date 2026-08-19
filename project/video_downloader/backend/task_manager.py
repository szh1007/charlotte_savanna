"""内存态任务存储 + 状态机流转 + 并发调度 (线程安全, ADR-0003).

任务状态机 (见 CONTEXT.md):
    pending → resolving → resolved → queued → downloading → completed
                              ↘                ↘
                               failed            failed
    completed → expired (交付链接过期, 文件被清理; failed 无交付资产, 保持终态)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from yt_dlp.utils import DownloadError

from . import config, downloader
from .events import bus, task_event

# 任务状态常量 (领域状态机)
STATUS_PENDING = "pending"
STATUS_RESOLVING = "resolving"
STATUS_RESOLVED = "resolved"
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"

# 移除事件标记 (非状态机状态): 任务从存储清除 (清除记录), SSE 广播后前端
# 从列表移除卡片. 不进状态机: 移除即不存在, 无需落任务状态
STATUS_REMOVED = "removed"

# 付费差异 (T05, PRD §5): 免费/会员能力边界, 后端强制, 非 UI 摆设
FREE_MAX_HEIGHT = 720  # 免费档清晰度上限: >720p 档位标记锁定
FREE_CONCURRENCY = 1  # 免费档并发下载槽位
MEMBER_CONCURRENCY = 3  # 会员档并发下载槽位
FREE_QUEUE_LIMIT = 5  # 免费档批量队列上限
MEMBER_QUEUE_LIMIT = 50  # 会员档批量队列上限

# 调度器扫描间隔 (秒): 把 queued 任务分配到空闲槽位 (PRD 设计值约 0.5s)
_SCHEDULER_INTERVAL = 0.5


class QueueLimitError(Exception):
    """队列超限 (路由层转为 429 + 明确提示)."""


@dataclass
class Task:
    id: int
    url: str
    kind: str  # "resolve" | "download"
    is_member: bool = False  # 创建者会员身份快照 (档位锁定/并发/队列按此计算)
    status: str = STATUS_PENDING
    title: str | None = None
    cover: str | None = None
    duration: float | None = None
    site: str | None = None
    formats: list[dict[str, Any]] = field(default_factory=list)
    format_id: str | None = None
    merge_audio: bool = False  # 档位为 DASH 分离流 (video-only): 下载时合并音频流
    progress: float = 0.0
    message: str | None = None
    file_path: str | None = None
    error: str | None = None
    completed_at: float | None = None  # 完成时刻 (TTL 过期起点, T06)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # 取消信号: 清除记录时置位, 下载中任务的 progress hook 检查后中断引擎
    cancel_event: threading.Event = field(default_factory=threading.Event)


class TaskManager:
    """任务存储、状态更新与下载调度; 所有操作持有 RLock 保证线程安全."""

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._lock = threading.RLock()
        self._seq = 0
        # 各身份的并发槽占用 (免费/会员槽位独立计算, 见 _scheduler_loop)
        self._active: dict[bool, int] = {False: 0, True: 0}
        self._scheduler: threading.Thread | None = None
        self._scheduler_stop = threading.Event()

    def create_task(self, url: str, kind: str, is_member: bool = False) -> Task:
        with self._lock:
            self._seq += 1
            task = Task(id=self._seq, url=url, kind=kind, is_member=is_member)
            self._tasks[task.id] = task
            return task

    def update_status(self, task_id: int, status: str, **fields: Any) -> Task | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                # 任务已被清除记录 (取消下载): 收尾更新跳过, 不广播
                return None
            task.status = status
            for key, value in fields.items():
                setattr(task, key, value)
            task.updated_at = time.time()
            # 状态变化即广播 (SSE 订阅者消费); 锁内 publish, 事件为当前状态快照
            bus.publish(task.id, task_event(task))
            return task

    def get_task(self, task_id: int) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[Task]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.id, reverse=True)
            return tasks[:limit]

    def list_completed(self) -> list[Task]:
        """全部 completed 任务 (TTL 清理扫描范围, 不设列表上限, T06)."""
        with self._lock:
            return [t for t in self._tasks.values() if t.status == STATUS_COMPLETED]

    def list_all(self) -> list[Task]:
        """全部任务快照 (清除过期记录 / 孤儿文件对照用, 无列表上限)."""
        with self._lock:
            return list(self._tasks.values())

    def remove_task(self, task_id: int) -> Task | None:
        """从存储移除任务 (清除记录) 并广播 removed 事件, 不存在返回 None.

        下载中任务先置取消信号: worker 的 progress hook 检查后抛异常中断
        引擎 (yt-dlp 清理临时文件, 任务 file_path 未回填无残留); 广播
        removed 后所有 SSE 订阅端 (多标签页) 同步移除该任务卡片.
        文件删除是路由层的锁外 IO, 不在本方法内执行.
        """
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if task is not None:
                task.cancel_event.set()
        if task is not None:
            bus.publish(task.id, {"task_id": task.id, "status": STATUS_REMOVED})
        return task

    def resolve(self, url: str, is_member: bool = False) -> Task:
        """同步解析链接: 任务状态 pending → resolving → resolved / failed.

        解析耗时通常在秒级, 同步等待引擎返回; 失败时任务标记 failed 并抛出
        ResolveError, 由路由层转为 4xx 响应. 按创建者身份标记档位锁定 (T05).
        """
        task = self.create_task(url=url, kind="resolve", is_member=is_member)
        self._fill_resolved(task)
        return self.get_task(task.id)

    def _fill_resolved(self, task: Task) -> dict[str, Any]:
        """执行解析并回填元信息 (resolve / create_download 共用解析段).

        档位锁定按任务创建者身份标记: 免费用户 >720p 档位 locked (PRD §5 强制校验点 1).
        """
        self.update_status(task.id, STATUS_RESOLVING)
        try:
            info = downloader.resolve(task.url)
        except downloader.ResolveError as e:
            self.update_status(task.id, STATUS_FAILED, error=str(e))
            raise
        formats = []
        for f in info.get("formats", []):
            fmt = dict(f)  # 复制, 避免影响引擎返回的原始数据
            fmt["locked"] = self._is_locked(fmt, task.is_member)
            formats.append(fmt)
        self.update_status(
            task.id,
            STATUS_RESOLVED,
            title=info["title"],
            cover=info.get("cover"),
            duration=info.get("duration"),
            site=info.get("site"),
            formats=formats,
        )
        return info

    def create_download(
        self, url: str, format_id: str, is_member: bool = False
    ) -> Task:
        """创建下载任务: 解析 → 校验档位 → 入队, 启动调度器.

        校验按身份执行 (T05): 免费用户选择 locked 档位 / 队列超限均拒绝,
        分别抛 ValueError / QueueLimitError, 由路由层转为 4xx 响应.
        """
        task = self.create_task(url=url, kind="download", is_member=is_member)
        info = self._fill_resolved(task)
        fmt = next(
            (f for f in info.get("formats", []) if f["format_id"] == format_id), None
        )
        if fmt is None:
            self.update_status(task.id, STATUS_FAILED, error=f"无效档位: {format_id}")
            raise ValueError(f"无效档位: {format_id}")
        if self._is_locked(fmt, is_member):
            self.update_status(task.id, STATUS_FAILED, error="该档位需会员解锁")
            raise ValueError("该档位需会员解锁")
        # DASH 分离流档位 (video-only, has_audio=False): 下载时合并音频流 (bugfix/0003)
        merge_audio = not fmt.get("has_audio", True)
        limit = self._queue_limit(is_member)
        # 按创建者身份计数, 排除当前任务自身 (检查时已处于 resolved 状态, 不应计入)
        if self._queue_size(is_member, exclude_id=task.id) >= limit:
            msg = f"队列已满 (上限 {limit} 个任务)"
            self.update_status(task.id, STATUS_FAILED, error=msg)
            raise QueueLimitError(msg)
        self.update_status(
            task.id, STATUS_QUEUED, format_id=format_id, merge_audio=merge_audio
        )
        self.ensure_scheduler()
        return self.get_task(task.id)

    @staticmethod
    def _is_locked(fmt: dict[str, Any], is_member: bool) -> bool:
        """档位对指定身份是否锁定: 免费用户 >720p 档位锁定 (PRD §5)."""
        return not is_member and (fmt.get("height") or 0) > FREE_MAX_HEIGHT

    def _queue_size(self, is_member: bool, exclude_id: int | None = None) -> int:
        """当前指定身份的未完成下载任务数 (队列占用, 终态不占位).

        按任务创建者身份分别计数: 免费档上限只约束免费用户自己的任务,
        不被排队中的会员任务挤占 (反之亦然). exclude_id 排除创建中的新任务,
        避免自占位.
        """
        with self._lock:
            return sum(
                1
                for t in self._tasks.values()
                if t.kind == "download"
                and t.is_member == is_member
                and t.id != exclude_id
                and t.status not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_EXPIRED)
            )

    def _queue_limit(self, is_member: bool) -> int:
        """队列上限按身份计算 (免费 5 / 会员 50)."""
        return MEMBER_QUEUE_LIMIT if is_member else FREE_QUEUE_LIMIT

    @staticmethod
    def _concurrency_limit(is_member: bool) -> int:
        """并发槽位上限按身份计算 (免费 1 / 会员 3)."""
        return MEMBER_CONCURRENCY if is_member else FREE_CONCURRENCY

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
        """周期扫描: 有空闲并发槽时, 把排队任务派发给执行线程.

        并发槽按身份独立计算 (免费 1 / 会员 3), 槽位空闲才派发对应身份的任务.
        """
        while not self._scheduler_stop.is_set():
            task = None
            with self._lock:
                task = self._next_queued()
                if task:
                    self._active[task.is_member] += 1
                    self.update_status(task.id, STATUS_DOWNLOADING)
            if task:
                worker = threading.Thread(
                    target=self._run_download,
                    args=(task.id, task.is_member),
                    name=f"download-worker-{task.id}",
                    daemon=True,
                )
                worker.start()
            else:
                self._scheduler_stop.wait(_SCHEDULER_INTERVAL)

    def _next_queued(self) -> Task | None:
        """返回槽位可用的最早排队任务: 会员优先, 同身份内按任务 id FIFO.

        必须跳过槽位已满的身份 (如会员槽满时队首的会员任务), 否则该身份
        槽位空闲的其他任务 (免费任务) 会被队首任务永久阻塞 (队首阻塞饥饿).
        """
        for t in sorted(self._tasks.values(), key=lambda t: (not t.is_member, t.id)):
            if t.status == STATUS_QUEUED:
                limit = self._concurrency_limit(t.is_member)
                if self._active[t.is_member] < limit:
                    return t
        return None

    def _run_download(self, task_id: int, is_member: bool) -> None:
        """执行下载并更新任务状态 (独立线程, 完成后释放并发槽).

        执行前重校验档位访问权 (PRD §5 强制校验点 3): 即使创建时被绕过
        (伪造请求 / 内部篡改), 非会员任务的锁定档位也会在下载前被拒绝.
        任务清除记录后 (取消): 引擎 hook 抛异常退出, 收尾更新由 update_status
        防御跳过; is_member 在派发时捕获 (任务可能已被移除, 槽位按此释放).
        """
        try:
            task = self.get_task(task_id)
            if task is None:  # 任务已被清除记录 (取消), worker 直接退出
                return
            self._validate_download_access(task)
            path = downloader.download(
                task.url,
                task.format_id,
                config.DOWNLOADS_DIR,
                self._progress_hook(
                    task_id,
                    merge_audio=task.merge_audio,
                    cancel_event=task.cancel_event,
                ),
                merge_audio=task.merge_audio,
            )
            self.update_status(
                task_id,
                STATUS_COMPLETED,
                file_path=path,
                progress=100.0,
                message="下载完成",
                completed_at=time.time(),  # TTL 起点: 文件就绪时刻 (T06)
            )
        except downloader.DownloadError as e:
            self.update_status(task_id, STATUS_FAILED, error=str(e))
        except ValueError as e:  # 档位访问重校验失败: 明确错误而非泛化「下载异常」
            self.update_status(task_id, STATUS_FAILED, error=str(e))
        except Exception as e:  # 引擎外异常 (磁盘/中断等): 标记失败而非悬挂
            self.update_status(task_id, STATUS_FAILED, error=f"下载异常: {e}")
        finally:
            with self._lock:
                # 任务可能已被清除 (取消): 槽位始终按派发时的身份释放
                self._active[is_member] -= 1

    def _validate_download_access(self, task: Task) -> None:
        """下载前档位重校验: 非会员任务选择锁定档位 → 拒绝 (纵深防御)."""
        if task.is_member:
            return
        fmt = next((f for f in task.formats if f["format_id"] == task.format_id), None)
        if fmt is not None and self._is_locked(fmt, task.is_member):
            raise ValueError("该档位需会员解锁")

    def _progress_hook(
        self,
        task_id: int,
        merge_audio: bool = False,
        cancel_event: threading.Event | None = None,
    ):
        """yt-dlp 进度回调: 转发到任务 progress / message (上限 99, 完成时置 100).

        合并下载 (merge_audio) 时 yt-dlp 先后下载视频流与音频流, 各自独立上报
        进度; 直接透传会让音频流把整体进度重置回 0 (前端进度条来回跳, bugfix/0004).
        按流均分合成整体进度: 首个流 0~50%, 第二个流 50~100%, 全程单调不减.
        """

        stream_keys: list[str] = []  # 已出现的流标识 (format_id, 合并场景最多 2 流)

        def hook(d: dict[str, Any]) -> None:
            # 清除记录已置取消信号: hook 抛异常中断引擎下载 (yt-dlp 官方
            # 取消方式), 临时文件由引擎清理; 任务已从存储移除, 收尾更新
            # 由 update_status 防御跳过 (bugfix/0007)
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadError("已取消")
            if d.get("status") != "downloading":
                return
            # 未知总量时引擎提供 total_bytes_estimate, 兜底避免进度恒为 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            percent = (done / total * 100) if total else 0.0
            if merge_audio:
                key = (d.get("info_dict") or {}).get("format_id") or ""
                if key and key not in stream_keys:
                    stream_keys.append(key)
                if stream_keys:
                    idx = stream_keys.index(key) if key in stream_keys else 0
                    # 首个流下载期间未知后续流数, 按双流均分占位; 无音频平台
                    # 回退单流时下载完成即置 100 (完成瞬间补满, 仅该场景跳变)
                    n = max(len(stream_keys), 2)
                    percent = (idx + percent / 100) / n * 100
            self.update_status(
                task_id,
                STATUS_DOWNLOADING,
                progress=min(percent, 99.0),
                message=f"下载中 {percent:.0f}%",
            )

        return hook


# 模块级单例: 路由与测试共享同一任务存储
manager = TaskManager()
