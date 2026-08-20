"""SSE 事件总线: 后台线程 → asyncio 循环的线程安全广播 (T03).

任务状态更新发生在后台调度线程, SSE 订阅者位于 asyncio 事件循环.
EventBus.publish 把事件经目标 loop 的 call_soon_threadsafe 投递到订阅队列,
跨线程无竞态; 订阅者 (SSE 路由生成器) 断开时自行 unsubscribe.
队列有界 (慢客户端丢弃过旧事件, 事件为状态快照, 消费端以下一帧覆盖).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:
    from .task_manager import Task


@dataclass
class Subscriber:
    """单个 SSE 连接的订阅: 独立事件队列 + 所在事件循环 + 关注的任务集."""

    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop
    task_ids: set[int] | None = None  # None = 关注全部任务

    def accepts(self, task_id: int) -> bool:
        """该订阅者是否关注指定任务 (?task_ids 过滤, 缺省关注全部)."""
        return self.task_ids is None or task_id in self.task_ids


class EventBus:
    """订阅中心: 任意线程 publish, 广播到所有匹配的订阅者队列 (锁保护)."""

    # 订阅队列上限: 进度事件高频, 消费慢的客户端丢弃过旧事件
    _QUEUE_MAXSIZE = 100

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[Subscriber] = []

    def subscribe(
        self, loop: asyncio.AbstractEventLoop, task_ids: set[int] | None = None
    ) -> Subscriber:
        """注册订阅者 (SSE 连接建立时, 在 asyncio 线程调用)."""
        sub = Subscriber(
            queue=asyncio.Queue(maxsize=self._QUEUE_MAXSIZE),
            loop=loop,
            task_ids=task_ids,
        )
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        """移除订阅者 (SSE 连接断开时, 在生成器 finally 中调用)."""
        with self._lock:
            self._subs = [s for s in self._subs if s is not sub]

    def publish(self, task_id: int, event: dict) -> None:
        """广播事件: 后台线程调用, 经目标 loop 线程安全投递, 不阻塞."""
        with self._lock:
            subs = [s for s in self._subs if s.accepts(task_id)]
        self._deliver(subs, event)

    def publish_all(self, event: dict) -> None:
        """广播给全部订阅者 (不受 task_id 过滤影响, ADR-0006 model-update).

        模型进度是全局资产状态, 订阅者即使只关注指定任务也应收到的
        模型进度事件走此通道; 其余语义同 publish.
        """
        with self._lock:
            subs = list(self._subs)
        self._deliver(subs, event)

    @staticmethod
    def _deliver(subs: list[Subscriber], event: dict) -> None:
        """投递事件到订阅者队列 (队列满由 _enqueue 丢弃, 不阻塞调用方)."""
        for sub in subs:
            if sub.loop.is_closed():
                continue  # 服务停机中: 丢弃广播 (worker 线程处于退出阶段)
            sub.loop.call_soon_threadsafe(_enqueue, sub, event)


def _enqueue(sub: Subscriber, event: dict) -> None:
    """投递事件到订阅队列 (在目标 loop 线程执行); 队列满时丢弃."""
    # 事件为状态快照, 过旧进度可安全丢弃
    with contextlib.suppress(asyncio.QueueFull):
        sub.queue.put_nowait(event)


def task_event(task: Task) -> dict:
    """Task → SSE 事件负载 (API 契约: task_id/status/title/cover/progress/...).

    title/cover 为解析完成的元信息 (resolving 阶段为空, 前端据此补全卡片);
    expires_at 仅完成时刻携带 (前端倒计时起点); 移除事件见 task_manager
    remove_task (status=removed, 独立负载无本函数字段). event 键为 SSE
    event 名标记 (路由帧编码用), 不随 data 透传.
    """
    return {
        "event": "task-update",
        "task_id": task.id,
        "status": task.status,
        "title": task.title or "",
        "cover": task.cover,
        "progress": task.progress,
        # 四标签独立进度 (总结任务; 下载任务恒 0/0)
        "transcript_progress": task.transcript_progress,
        "summary_progress": task.summary_progress,
        # 四子任务状态 (kind=summary; 下载任务为空 dict), 前端逐 tab 驱动
        "subtasks": {
            name: {
                "status": sub.status,
                "progress": sub.progress,
                "error": sub.error,
                "message": sub.message,
            }
            for name, sub in task.subtasks.items()
        },
        # 视频元信息 (卡片展示)
        "uploader": task.uploader,
        "view_count": task.view_count,
        "description": task.description,
        "message": task.message,
        # 交付直链 (url 键已被占用, 源链接走 source_url, 前端按它分组)
        "url": f"/api/files/{task.id}" if task.file_path else None,
        "source_url": task.url,
        "error": task.error,
        "expires_at": (
            task.completed_at + config.delivery_ttl(task.is_member)
            if task.completed_at
            else None
        ),
    }


def model_update_event(status: str, progress: float) -> dict:
    """模型状态 → SSE 事件负载 (ADR-0006): event 名标记 + 状态 + 进度."""
    return {"event": "model-update", "status": status, "progress": progress}


# 模块级单例: task_manager 状态更新后 publish, SSE 路由订阅
bus = EventBus()
