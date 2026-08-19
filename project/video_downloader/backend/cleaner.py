"""TTL 后台清理 (T06, ADR-0003): 周期扫描过期交付任务, 删文件 + 标记 expired.

免费任务 24h / 会员任务 72h (PRD §5), 按任务创建者身份快照计算,
不依赖当前会话状态. 时间判定用可注入时钟 _now() (测试推进时间,
与 auth._now 同一模式, 无需真实等待).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import config
from .task_manager import STATUS_COMPLETED, STATUS_EXPIRED, TaskManager, manager

# 清理扫描周期 (秒): 后台线程每周期全量扫描一次 (PRD 设计值约 60s)
_CLEANER_INTERVAL = 60

logger = logging.getLogger(__name__)


def _now() -> float:
    """可注入时钟: 测试推进时间验证 TTL 过期 (与 auth._now 同一模式)."""
    return time.time()


class DeliveryCleaner:
    """过期交付清理器: 周期扫描 + 幂等清理 (复用任务存储锁, 线程安全)."""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动清理线程 (daemon, 幂等: 已在运行则 no-op)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="delivery-cleaner", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """停止清理线程 (服务退出 / 测试隔离时调用, 幂等).

        不 join: 线程在下一个 wait 周期内自行退出 (daemon, 进程退出即终止).
        """
        self._stop.set()

    def _loop(self) -> None:
        """周期扫描: 每周期清理一次过期任务, 空闲时等待."""
        while not self._stop.is_set():
            self.cleanup_expired()
            self._stop.wait(_CLEANER_INTERVAL)

    def cleanup_expired(self) -> list[int]:
        """扫描超 TTL 的 completed 任务: 删除交付文件 + 标记 expired (幂等).

        已 expired 任务不在扫描范围, 重复调用无副作用; 文件删除失败
        (权限/占用等异常) 记录日志但不阻塞过期标记, 避免任务永驻 completed
        且清理线程被异常杀死 (无人复活).
        """
        expired: list[int] = []
        for task in self._manager.list_completed():
            if task.completed_at is None:
                continue  # 防御: completed 任务必然有完成时刻, 无则不判
            ttl = self._delivery_ttl(task.is_member)
            if _now() - task.completed_at < ttl:
                continue  # 未过期: 不处理
            if task.file_path:
                try:
                    Path(task.file_path).unlink(missing_ok=True)  # 锁外 IO, 幂等
                except OSError as e:
                    # 文件被占用/权限拒绝: 不抛异常 (否则线程死亡, 清理停止)
                    logger.warning(
                        "清理交付文件失败 task=%s path=%s: %s",
                        task.id,
                        task.file_path,
                        e,
                    )
            # file_path 置 None: 事件负载 url 随即失效, 直链路由亦已拒绝
            self._manager.update_status(
                task.id,
                STATUS_EXPIRED,
                file_path=None,
                message="交付链接已过期, 文件已清理",
            )
            expired.append(task.id)
        return expired

    def purge_unfinished(self) -> list[int]:
        """清除全部未完成记录 (用户一键触发): 移除任务 + 删除残留文件.

        未完成 = 无有效交付资产: expired / failed / 排队中 / 下载中 (取消) /
        已超 TTL 的 completed (周期清理未及处理); 仅保留可交付的 completed.
        下载中任务经 remove_task 置取消信号, 引擎中断并清理临时文件.
        顺带清理孤儿文件: DOWNLOADS_DIR 中无任何任务引用且超过 24h
        未修改的文件 (手动清除时删除失败 / 进程崩溃残留的 .part 等).
        返回被移除的任务 id 列表, 幂等.
        """
        removed: list[int] = []
        now = _now()
        for task in self._manager.list_all():
            if task.status == STATUS_COMPLETED and not self._is_overdue(task, now):
                continue  # 已完成且未过期: 保留 (其余状态均视为未完成)
            self._delete_file(task)
            self._manager.remove_task(task.id)  # 移除即广播 removed; 下载中置取消信号
            removed.append(task.id)
        self._cleanup_orphan_files(now)
        return removed

    def _is_overdue(self, task, now: float) -> bool:
        """completed 任务是否已超 TTL (expired 状态无需判定, 直接清除)."""
        return (
            task.completed_at is not None
            and now - task.completed_at >= self._delivery_ttl(task.is_member)
        )

    @staticmethod
    def _delete_file(task) -> None:
        """删除任务交付文件 (锁外 IO, 幂等; 失败仅记录日志, 任务照常移除)."""
        if not task.file_path:
            return
        try:
            Path(task.file_path).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(
                "删除交付文件失败 task=%s path=%s: %s", task.id, task.file_path, e
            )

    def _cleanup_orphan_files(self, now: float) -> None:
        """删除无任务引用的孤儿文件 (超过 24h 未修改).

        覆盖场景: 手动清除记录时文件删除失败 / 进程崩溃残留的 .part 等.
        年龄门槛 (24h) 保护正在下载的文件 — 下载中任务 file_path 未回填,
        无引用但文件在活跃写入, mtime 为近期不会误删.
        """
        referenced = {
            Path(t.file_path)
            for t in self._manager.list_all()
            if t.file_path is not None
        }
        try:
            files = list(config.DOWNLOADS_DIR.iterdir())
        except OSError:
            return  # 目录不存在等: 无文件可清理
        for path in files:
            if path in referenced:
                continue
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age > config.FREE_DELIVERY_TTL:
                try:
                    path.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("清理孤儿文件失败 path=%s: %s", path, e)

    @staticmethod
    def _delivery_ttl(is_member: bool) -> float:
        """交付直链有效期按身份计算 (免费 24h / 会员 72h, PRD §5)."""
        return config.delivery_ttl(is_member)


# 模块级单例: main lifespan 启动, 路由 / 测试共享
cleaner = DeliveryCleaner(manager=manager)
