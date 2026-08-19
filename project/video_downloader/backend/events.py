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
    """Task → SSE 事件负载 (API 契约: task_id/status/progress/message/url?/error?)."""
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "url": f"/api/files/{task.id}" if task.file_path else None,
        "error": task.error,
    }


# 模块级单例: task_manager 状态更新后 publish, SSE 路由订阅
bus = EventBus()
