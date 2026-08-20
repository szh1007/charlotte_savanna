"""内存态任务存储 + 状态机流转 + 并发调度 (线程安全, ADR-0003).

任务状态机 (见 CONTEXT.md):
    pending → resolving → resolved → queued → downloading → completed
                              ↘                ↘
                               failed            failed
    completed → expired (交付链接过期, 文件被清理; failed 无交付资产, 保持终态)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from yt_dlp.utils import DownloadError

from . import asr, config, downloader, llm, model_downloader, subtitle, subtitle_cache
from .events import bus, task_event
from .quota import SUMMARY as QUOTA_KIND_SUMMARY
from .quota import quota as daily_quota

logger = logging.getLogger(__name__)

# 任务状态常量 (领域状态机)
STATUS_PENDING = "pending"
STATUS_RESOLVING = "resolving"
STATUS_RESOLVED = "resolved"
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
# 总结任务运行态 (ADR-0005): 四子任务按依赖链推进 (转录→总结→导图/问答),
# 任务级仅标记「运行中」, 各子任务进度独立 (见 Subtask)
STATUS_RUNNING = "running"

# 移除事件标记 (非状态机状态): 任务从存储清除 (清除记录), SSE 广播后前端
# 从列表移除卡片. 不进状态机: 移除即不存在, 无需落任务状态
STATUS_REMOVED = "removed"

# 总结子任务标识 (ADR-0005 四标签): 转录/总结/思维导图/问答上下文
SUBTASK_TRANSCRIPT = "transcript"
SUBTASK_SUMMARY = "summary"
SUBTASK_MINDMAP = "mindmap"
SUBTASK_QA = "qa"
SUBTASK_NAMES = (SUBTASK_TRANSCRIPT, SUBTASK_SUMMARY, SUBTASK_MINDMAP, SUBTASK_QA)

# 字幕来源 (ADR-0006 创建者快照): official = 官方字幕快路径 / model = 模型生成
SUBTITLE_SOURCE_OFFICIAL = "official"
SUBTITLE_SOURCE_MODEL = "model"

# 子任务状态 (独立于任务级状态机, 前端逐 tab 驱动)
ST_PENDING = "pending"  # 未开始 (含重试重置)
ST_RUNNING = "running"  # 执行中
ST_DONE = "done"  # 成功
ST_FAILED = "failed"  # 自身失败 (可重试)
ST_BLOCKED = "blocked"  # 依赖子任务失败, 依赖恢复后自动重跑

# 子任务 DAG 依赖 (用户反馈: 导图用总结后的数据, 不再直接用字幕):
# transcript 无依赖; summary 依赖转录 (字幕 → 总结); mindmap 依赖总结
# (总结 → 导图); qa 依赖转录 + 总结, 总结完成后即解锁 (无需等导图)
SUBTASK_DEPS: dict[str, frozenset[str]] = {
    SUBTASK_TRANSCRIPT: frozenset(),
    SUBTASK_SUMMARY: frozenset({SUBTASK_TRANSCRIPT}),
    SUBTASK_MINDMAP: frozenset({SUBTASK_SUMMARY}),
    SUBTASK_QA: frozenset({SUBTASK_TRANSCRIPT, SUBTASK_SUMMARY}),
}

# 任务级整体进度加权映射 (旧契约 progress 字段, 各子任务 progress 0-100)
_SUBTASK_WEIGHTS = {
    SUBTASK_TRANSCRIPT: 0.4,
    SUBTASK_SUMMARY: 0.2,
    SUBTASK_MINDMAP: 0.2,
    SUBTASK_QA: 0.2,
}

# 付费差异 (T05, PRD §5): 免费/会员能力边界, 后端强制, 非 UI 摆设
FREE_MAX_HEIGHT = 720  # 免费档清晰度上限: >720p 档位标记锁定
FREE_CONCURRENCY = 1  # 免费档并发下载槽位
MEMBER_CONCURRENCY = 3  # 会员档并发下载槽位
FREE_QUEUE_LIMIT = 5  # 免费档批量队列上限
MEMBER_QUEUE_LIMIT = 50  # 会员档批量队列上限

# 调度器扫描间隔 (秒): 把 queued 任务分配到空闲槽位 (PRD 设计值约 0.5s)
_SCHEDULER_INTERVAL = 0.5

# 字幕重排分块字符上限 (LLM 单次调用输入量): 超长转录按块逐次重排,
# 块范围 = 块内首/末句时间戳 (重排输出时间戳线性均匀分布在此范围内)
POLISH_CHUNK_CHARS = 1500


class QueueLimitError(Exception):
    """队列超限 (路由层转为 429 + 明确提示)."""


@dataclass
class Subtask:
    """总结任务子任务状态 (ADR-0005): 四标签独立进度/错误, 独立失败与重试."""

    name: str
    status: str = ST_PENDING
    progress: float = 0.0
    error: str | None = None
    message: str | None = None


@dataclass
class Task:
    id: int
    url: str
    kind: str  # "resolve" | "download" | "summary"
    is_member: bool = False  # 创建者会员身份快照 (档位锁定/并发/队列按此计算)
    status: str = STATUS_PENDING
    title: str | None = None
    cover: str | None = None
    duration: float | None = None
    site: str | None = None
    formats: list[dict[str, Any]] = field(default_factory=list)
    format_id: str | None = None
    # 「最佳画质」伪档 (format_id="best") 映射的真实最高档 id, 创建时固化
    # (任务展示列表已裁剪该伪档, worker 无法从 formats 回溯, 见 _visible_formats)
    real_format_id: str | None = None
    merge_audio: bool = False  # 档位为 DASH 分离流 (video-only): 下载时合并音频流
    progress: float = 0.0
    message: str | None = None
    file_path: str | None = None
    error: str | None = None
    completed_at: float | None = None  # 完成时刻 (TTL 过期起点, T06)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # 视频元信息 (解析时回填, 前端卡片展示: up主/播放量/简介)
    uploader: str | None = None
    view_count: int | None = None
    description: str | None = None
    # 总结任务结果 (ADR-0005): transcript = [{start, end, text}],
    # summary = 结构化总结 JSON, mindmap = 由总结生成的导图结构 JSON
    transcript: list[dict[str, Any]] | None = None
    summary: dict[str, Any] | None = None
    mindmap: dict[str, Any] | None = None
    # 总结流式缓冲 (ADR-0007): worker 锁内 append 增量 chunk, SSE 端点锁内
    # 快照轮询 (summary_stream_snapshot); 重试/重跑前清空
    summary_stream: list[str] = field(default_factory=list)
    # 字幕重排流式缓冲 (模型生成增强): 重排期间 LLM 增量实时写入, SSE 端点
    # 快照轮询 (transcript_stream_snapshot); 重排失败降级时清空
    transcript_stream: list[str] = field(default_factory=list)
    # 四子任务状态 (kind=summary; 下载任务为空 dict), 事件负载逐 tab 推送
    subtasks: dict[str, Subtask] = field(default_factory=dict)
    # 四标签独立进度 (旁路字段, 兼容旧契约): subtasks 状态的平铺镜像,
    # 由 update_subtask 同步赋值. 转录 0-100 (ASR 回调粒度, 字幕快路径
    # 瞬时置 100) / 总结生成中置 30, 完成置 100
    transcript_progress: float = 0.0
    summary_progress: float = 0.0
    # 字幕来源快照 (ADR-0006): official = 官方字幕快路径 / model = 模型生成,
    # 创建者选择在创建时固化, 重试沿用
    subtitle_source: str = SUBTITLE_SOURCE_OFFICIAL
    # 创建者匿名身份 (字幕缓存命中退还配额用, 会员为空)
    client_id: str | None = None
    # 缓存命中配额是否已退还 (幂等: 重试不重复退还)
    quota_refunded: bool = False
    # 取消信号: 清除记录时置位, 下载中任务的 progress hook 检查后中断引擎
    cancel_event: threading.Event = field(default_factory=threading.Event)


def _clean_description(info: dict[str, Any]) -> str | None:
    """简介清洗: 引擎占位值 '-' / 空串视为无简介 (前端两行省略展示).

    B 站部分视频简介为空, yt-dlp 归一化为 '-' 占位, 直接透传会让
    前端显示无意义的单字符.
    """
    desc = (info.get("description") or "").strip()
    if desc in ("", "-"):
        return None
    return desc[:500]


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
        # 已派发 worker 线程追踪 (测试隔离 join 用): 残留 worker 可能在
        # 上一测试 monkeypatch 撤销后继续运行 (转录线程触发真实 ASR 下载)
        self._workers: set[threading.Thread] = set()

    def create_task(self, url: str, kind: str, is_member: bool = False) -> Task:
        with self._lock:
            self._seq += 1
            task = Task(id=self._seq, url=url, kind=kind, is_member=is_member)
            # 总结任务初始化四子任务 (pending), 供调度器 DAG 扫描派发
            if kind == "summary":
                task.subtasks = {name: Subtask(name=name) for name in SUBTASK_NAMES}
            self._tasks[task.id] = task
            return task

    def update_subtask(
        self,
        task_id: int,
        name: str,
        status: str,
        progress: float | None = None,
        error: str | None = None,
        message: str | None = None,
    ) -> None:
        """更新单个子任务状态 + 同步平铺镜像字段 + 广播 SSE (锁内 publish).

        任务被清除记录后静默跳过 (worker 收尾更新, 与 update_status 同款防御).
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            sub = task.subtasks.get(name)
            if sub is None:
                return
            sub.status = status
            if progress is not None:
                sub.progress = progress
            if error is not None:
                sub.error = error
            if message is not None:
                sub.message = message
            # 平铺镜像: 旧契约字段 (SSE/TaskOut 兼容), 其余子任务不占位
            if name == SUBTASK_TRANSCRIPT:
                task.transcript_progress = sub.progress
            elif name == SUBTASK_SUMMARY:
                task.summary_progress = sub.progress
            task.progress = self._weighted_progress(task)
            task.updated_at = time.time()
            bus.publish(task.id, task_event(task))

    @staticmethod
    def _weighted_progress(task: Task) -> float:
        """任务级整体进度 = 各子任务进度加权和 (兼容旧契约 progress 字段)."""
        return round(
            sum(
                _SUBTASK_WEIGHTS[name] * sub.progress
                for name, sub in task.subtasks.items()
            ),
            1,
        )

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

    def summary_stream_snapshot(
        self,
        task_id: int,
    ) -> tuple[str, str | None, list[str]] | None:
        """总结流快照: (子任务状态, 错误, 缓冲 chunk 副本); 任务不存在返回 None.

        SSE 端点每轮轮询调用: 状态推进与文本 append 同锁, 快照为同一时刻
        一致视图; 返回副本避免端点持有期间被 worker 改写 (ADR-0007).
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            sub = task.subtasks.get(SUBTASK_SUMMARY)
            return (
                sub.status if sub is not None else ST_PENDING,
                sub.error,
                list(task.summary_stream),
            )

    def transcript_stream_snapshot(
        self,
        task_id: int,
    ) -> tuple[str, str | None, list[str]] | None:
        """字幕重排流快照: (转录子任务状态, 错误, 缓冲 chunk 副本); 任务不存在返回 None.

        SSE 端点每轮轮询调用, 语义与 summary_stream_snapshot 一致 (ADR-0007):
        状态推进与文本 append 同锁, 返回副本避免端点持有期间被 worker 改写.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            sub = task.subtasks.get(SUBTASK_TRANSCRIPT)
            return (
                sub.status if sub is not None else ST_PENDING,
                sub.error,
                list(task.transcript_stream),
            )

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
        for f in self._visible_formats(info.get("formats", []), task.is_member):
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
            uploader=info.get("uploader"),
            view_count=info.get("view_count"),
            description=_clean_description(info),
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
        # 「最佳画质」伪档: 真实档位 id 固化到任务 (任务展示列表已裁剪
        # 该伪档, worker 无法从 formats 回溯, 见 _visible_formats)
        real_format_id = fmt.get("real_format_id") if format_id == "best" else None
        limit = self._queue_limit(is_member)
        # 按创建者身份计数, 排除当前任务自身 (检查时已处于 resolved 状态, 不应计入)
        if self._queue_size(is_member, exclude_id=task.id) >= limit:
            msg = f"队列已满 (上限 {limit} 个任务)"
            self.update_status(task.id, STATUS_FAILED, error=msg)
            raise QueueLimitError(msg)
        self.update_status(
            task.id,
            STATUS_QUEUED,
            format_id=format_id,
            merge_audio=merge_audio,
            real_format_id=real_format_id,
        )
        self.ensure_scheduler()
        return self.get_task(task.id)

    def create_summary(
        self,
        url: str,
        is_member: bool = False,
        subtitle_source: str = SUBTITLE_SOURCE_OFFICIAL,
        client_id: str | None = None,
    ) -> Task:
        """创建总结任务 (kind=summary, ADR-0005): 解析元信息 → 入队, 启动调度器.

        元信息解析 (标题/封面/时长) 供任务卡片展示, 失败不阻塞总结
        (转录与总结不依赖标题, 记日志不静默). 配额已在路由层检查 (免费
        按 client_id), 总结任务不受批量队列上限约束 (每日配额已限制滥用),
        与下载任务共享并发槽位 (CPU 密集, 复用调度器).

        subtitle_source 为创建者选择快照 (ADR-0006 决策 1: 全局设置创建时
        固化), 转录子任务按此走官方快路径或模型生成; client_id 用于字幕
        缓存命中时退还配额 (创建时先扣, 命中净消耗 0, 防滥用).
        """
        task = self.create_task(url=url, kind="summary", is_member=is_member)
        task.subtitle_source = subtitle_source
        task.client_id = client_id
        try:  # 轻量解析: 卡片元信息 + 档位列表, 失败不阻塞总结 (总结流程不依赖标题)
            info = downloader.resolve(url)
            # 档位回填: 仅 AI 总结不下载时, 下载区仍展示各清晰度 (锁定状态, 用户
            # 反馈); 锁定标记与下载任务同规则 (免费 >720p locked, 同 _fill_resolved)
            formats = [
                {**f, "locked": self._is_locked(f, task.is_member)}
                for f in self._visible_formats(info.get("formats", []), task.is_member)
            ]
            self.update_status(
                task.id,
                STATUS_PENDING,
                title=info["title"],
                cover=info.get("cover"),
                duration=info.get("duration"),
                site=info.get("site"),
                uploader=info.get("uploader"),
                view_count=info.get("view_count"),
                description=_clean_description(info),
                formats=formats,
            )
        except downloader.ResolveError as e:
            logger.warning("总结任务元信息解析失败 task=%s: %s", task.id, e)
        self.update_status(task.id, STATUS_QUEUED)
        self.ensure_scheduler()
        return self.get_task(task.id)

    def find_active_summary(self, url: str) -> Task | None:
        """同 url 的活跃总结任务 (queued/running) → 返回 (创建接口幂等去重).

        URL 不归一化, 与前端任务分组键一致; 终态任务 (completed/failed/
        expired) 允许再次创建.
        """
        with self._lock:
            for t in self._tasks.values():
                if (
                    t.kind == "summary"
                    and t.url == url
                    and t.status in (STATUS_QUEUED, STATUS_RUNNING)
                ):
                    return t
        return None

    def retry_subtask(self, task_id: int, name: str) -> Task:
        """重试失败的子任务: 重置 pending 并重新入队, 任务回 queued 重新调度.

        只重跑该子任务 (done 子任务保留结果), 依赖它的 blocked 子任务在
        DAG 扫描时自动解锁. 仅 failed/blocked 可重试, 运行中/未开始拒绝
        (杜绝双 worker). 配额已在路由层跳过 (重试不扣).
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"任务不存在: {task_id}")
            if task.kind != "summary":
                raise ValueError("仅总结任务可重试子任务")
            sub = task.subtasks.get(name)
            if sub is None:
                raise ValueError(f"未知子任务: {name}")
            if sub.status not in (ST_FAILED, ST_BLOCKED):
                raise ValueError("子任务未失败, 无需重试")
            sub.status = ST_PENDING
            sub.progress = 0.0
            sub.error = None
            sub.message = None
            task.status = STATUS_QUEUED
            task.updated_at = time.time()
            bus.publish(task.id, task_event(task))
        self.ensure_scheduler()
        return self.get_task(task_id)

    @staticmethod
    def _visible_formats(
        formats: list[dict[str, Any]], is_member: bool
    ) -> list[dict[str, Any]]:
        """档位列表按身份裁剪: 一律不展示「最佳画质」伪档 (与真实最高档重复,
        用户反馈; 免费档保留锁定档位作解锁引导, 但伪档本身不展示);
        裁剪仅影响展示列表, 下载校验与 real_format_id 映射仍走引擎全量
        (downloader._to_formats 不动)."""
        return [f for f in formats if f.get("format_id") != "best"]

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

    def join_workers(self, timeout: float = 1.0) -> None:
        """等待已派发 worker 全部退出 (测试隔离用).

        残留 worker 的子任务线程 (如取消任务中轮询等待的转录线程) 可能在
        上一测试 monkeypatch 撤销后继续运行, 触发真实引擎调用 (ASR 下载
        33MB 音频 / 模型下载). 调用方应先 stop_scheduler 停止新派发,
        再 join 存量 worker; worker 等待子任务线程退出, join 即等其收尾.
        """
        with self._lock:
            workers = list(self._workers)
        for t in workers:
            t.join(timeout=timeout)

    def _scheduler_loop(self) -> None:
        """周期扫描: 有空闲并发槽时, 把排队任务派发给执行线程.

        并发槽按身份独立计算 (免费 1 / 会员 3), 槽位空闲才派发对应身份的任务.
        派发状态按任务类型区分: 下载 → downloading; 总结 → running
        (ADR-0005), 避免总结卡片闪「下载中」; 子任务进度走 subtasks 字段.
        """
        while not self._scheduler_stop.is_set():
            task = None
            with self._lock:
                task = self._next_queued()
                if task:
                    self._active[task.is_member] += 1
                    status = (
                        STATUS_RUNNING if task.kind == "summary" else STATUS_DOWNLOADING
                    )
                    self.update_status(task.id, status)
            if task:
                # 按任务类型分派 worker: 下载 / 总结 (ADR-0005) 共用并发槽
                runner = (
                    self._run_summary_worker
                    if task.kind == "summary"
                    else self._run_download
                )
                worker = threading.Thread(
                    target=runner,
                    args=(task.id, task.is_member),
                    name=f"{task.kind}-worker-{task.id}",
                    daemon=True,
                )
                with self._lock:
                    self._workers.add(worker)
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
            # 「最佳画质」档 (format_id="best") 是记录用独立 id, 实际下载用
            # real_format_id (真实最高档 id): 字面 "best" 是 yt-dlp 表达式,
            # B 站全 DASH 分离流下匹配为空 (bugfix/0003); 真实 id 在任务创建时
            # 固化 (任务 formats 已裁剪最佳画质伪档, 见 _visible_formats)
            format_id = task.real_format_id or task.format_id
            path = downloader.download(
                task.url,
                format_id,
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
                self._workers.discard(threading.current_thread())

    def _run_summary_worker(self, task_id: int, is_member: bool) -> None:
        """四子任务 DAG 执行 (独立线程, 完成后释放并发槽, ADR-0005).

        每轮扫描「可运行」子任务 (pending/blocked 且依赖全部 done), 并行启动
        线程执行, 等待期间轮询扫描新解锁子任务并立即启动 (mindmap/qa 仅
        依赖总结, 总结完成后无需互相等待即各自解锁), 直至无可运行 → 汇总
        终态. 转录先完成即可先行查看; 单子任务失败只标自身 failed, 依赖它
        的子任务标 blocked, 重试后自动解锁. 任务清除记录 (取消) 时收尾更新
        由 update_subtask 防御跳过, 槽位始终按派发时的身份释放.
        """
        try:
            while True:
                runnable = self._runnable_subtasks(task_id)
                if not runnable:
                    break
                threads = []
                started: set[str] = set()
                for name in runnable:
                    threads.append(self._spawn_subtask(task_id, name))
                    started.add(name)
                # 等待期间动态解锁: 整体 join 会阻塞到本轮全部子任务结束
                # (含慢的 mindmap), 导致 qa 必须等思维导图完成才能解锁
                # (用户反馈). 改为轮询: 依赖完成即启动新解锁子任务
                while threads:
                    for name in self._runnable_subtasks(task_id):
                        if name in started:
                            continue
                        started.add(name)
                        threads.append(self._spawn_subtask(task_id, name))
                    threads = [t for t in threads if t.is_alive()]
                    time.sleep(0.05)
            self._finalize_subtasks(task_id)
        finally:
            with self._lock:
                # 任务可能已被清除 (取消): 槽位始终按派发时的身份释放
                self._active[is_member] -= 1
                self._workers.discard(threading.current_thread())

    def _spawn_subtask(self, task_id: int, name: str) -> threading.Thread:
        """启动单个子任务线程并标记 running (worker 循环与动态解锁复用)."""
        self.update_subtask(task_id, name, ST_RUNNING, progress=0.0)
        t = threading.Thread(
            target=self._run_subtask,
            args=(task_id, name),
            name=f"subtask-{name}-{task_id}",
            daemon=True,
        )
        t.start()
        return t

    def _runnable_subtasks(self, task_id: int) -> list[str]:
        """锁内判定本轮可运行子任务: pending/blocked 且依赖全部 done.

        依赖 failed 或 blocked (含传递依赖, 如 mindmap→summary→transcript)
        而自身 pending 时标 blocked (等重试恢复, 不重复执行). 返回空列表
        表示 DAG 已无进展 (终态判定).
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return []
            runnable = []
            for name in SUBTASK_NAMES:
                sub = task.subtasks.get(name)
                if sub is None or sub.status not in (ST_PENDING, ST_BLOCKED):
                    continue
                deps = SUBTASK_DEPS[name]
                if all(task.subtasks[d].status == ST_DONE for d in deps):
                    runnable.append(name)
                elif (
                    any(
                        task.subtasks[d].status in (ST_FAILED, ST_BLOCKED) for d in deps
                    )
                    and sub.status == ST_PENDING
                ):  # 依赖失败/阻塞: 标阻塞等重试, 依赖恢复后自动解锁
                    sub.status = ST_BLOCKED
                    sub.message = "依赖子任务失败, 等待重试"
            return runnable

    def _run_subtask(self, task_id: int, name: str) -> None:
        """执行单个子任务 (独立线程): 转录/LLM 生成/问答上下文就绪标记.

        失败只标自身 failed, 不拖垮其余子任务; 明确异常 (转录/LLM) 透传原因,
        其余异常统一前缀 (与旧 _run_summary 失败语义一致).
        """
        task = self.get_task(task_id)
        if task is None:  # 任务已被清除记录 (取消), worker 直接退出
            return
        try:
            if name == SUBTASK_TRANSCRIPT:
                self._run_transcript_subtask(task)
            elif name in (SUBTASK_SUMMARY, SUBTASK_MINDMAP):
                self._run_llm_subtask(task, name)
            else:  # qa: 上下文 (转录+总结) 就绪即完成, 不调 LLM, 提问时实时回答
                self.update_subtask(
                    task_id,
                    name,
                    ST_DONE,
                    progress=100.0,
                    message="问答上下文已就绪",
                )
        except (asr.TranscriptError, llm.LLMError) as e:
            self.update_subtask(task_id, name, ST_FAILED, error=str(e))
        except Exception as e:
            self.update_subtask(task_id, name, ST_FAILED, error=f"{name} 异常: {e}")

    def _run_transcript_subtask(self, task: Task) -> None:
        """转录子任务 (ADR-0006 双路径): 官方字幕快路径 / 模型生成 (缓存优先).

        official: 官方字幕秒级提取 (不写缓存, 秒级获取无需缓存), 为空
        回退模型生成 — 仅校验模型存在, 缺失 → failed 提示先下载 (不自动
        触发 1GB 下载, 验收硬性要求); 回退语义同 model 路径 (查缓存 →
        转写 → 写缓存). model: 优先命中字幕缓存 (全局共享, 命中不另扣
        配额), 未命中 → 模型缺失自动触发下载 (进度可见, 任务取消不中断
        下载) → 转写 → 写缓存.
        """
        if task.subtitle_source == SUBTITLE_SOURCE_OFFICIAL:
            segments = subtitle.get_subtitles(task.url)
            if segments is not None:
                self._finish_transcript(task, segments, message="字幕获取完成")
                return
            self.update_subtask(
                task.id,
                SUBTASK_TRANSCRIPT,
                ST_RUNNING,
                progress=1.0,
                message="无官方字幕, 切换模型生成",
            )
            segments, polished_ok = self._transcribe_with_cache(
                task, auto_download=False
            )
        else:
            segments, polished_ok = self._transcribe_with_cache(
                task, auto_download=True
            )
        message = "字幕精修完成" if polished_ok else "字幕精修失败, 使用原始转写"
        self._finish_transcript(task, segments, message=message)

    def _finish_transcript(
        self, task: Task, segments: list[dict[str, Any]], message: str
    ) -> None:
        """转录成功收尾: 子任务置 done + 结果落 task (锁内写, 防与重试竞态)."""
        self.update_subtask(
            task.id, SUBTASK_TRANSCRIPT, ST_DONE, progress=100.0, message=message
        )
        with self._lock:
            if task.id in self._tasks:
                task.transcript = segments

    def _transcribe_with_cache(
        self, task: Task, auto_download: bool
    ) -> tuple[list[dict[str, Any]], bool]:
        """模型生成路径: 缓存优先 → 模型就绪保障 → 转写 → LLM 重排 → 写缓存.

        返回 (segments, polished_ok): polished_ok=False 表示重排失败降级
        (使用原始转写段, subtask message 已标注, 不阻塞转录/总结).

        auto_download=True (显式选模型生成): 模型缺失自动触发下载并等待
        (进度 0~50 可见, 下载失败 → 转录 failed); False (官方字幕回退):
        仅校验模型存在, 缺失 → failed 提示先下载模型 (不自动触发下载,
        避免回退路径隐性消耗 1GB 流量). 缓存命中返回缓存段 (创建时已扣
        配额, 命中退还 → 净消耗 0), 未命中转写 → 重排 → 落盘 (重排失败
        时落原始段, 均为最终交付字幕; BV 号解析失败则跳过写缓存).
        """
        cached = subtitle_cache.get(task.url)
        if cached is not None:
            self._refund_quota_on_cache_hit(task)
            return cached, True
        if not model_downloader.is_ready():
            if not auto_download:
                raise asr.TranscriptError("无官方字幕且模型未下载, 请先下载模型后重试")
            self._wait_model_ready(task)
        segments = asr.transcribe(
            task.url,
            self._transcript_progress(task.id, task.cancel_event),
            task.cancel_event,
        )
        try:
            polished = self._polish_segments(task, segments)
        except llm.LLMError as e:
            # 重排为增强步骤: 失败降级用原始段, 不影响字幕可用性
            logger.warning("字幕重排失败, 降级使用原始转写: %s", e)
            self.update_subtask(
                task.id,
                SUBTASK_TRANSCRIPT,
                ST_RUNNING,
                progress=99.0,
                message="字幕精修失败, 使用原始转写",
            )
            polished, polished_ok = segments, False
        else:
            polished_ok = True
        subtitle_cache.put(task.url, polished, task.is_member)
        return polished, polished_ok

    def _polish_segments(
        self, task: Task, segments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """LLM 重排字幕 (模型生成增强): 逐块流式精修 → transcript_stream 缓冲.

        块间检查取消信号 (置位抛 TranscriptError, 语义同 ASR 转写取消);
        单块 LLM 失败抛 LLMError (调用方降级). 重排成功返回新段列表,
        时间戳由 LLM 在块范围内线性均匀生成 (parse_polished_lines 兜底).
        进度: 80~99 (转写阶段占 55~80, 见 _transcript_progress).
        """
        with self._lock:
            if task.id in self._tasks:
                task.transcript_stream.clear()
        chunks = _split_polish_chunks(segments, POLISH_CHUNK_CHARS)
        if not chunks:
            return segments
        polished: list[dict[str, Any]] = []
        for idx, (start, end, text) in enumerate(chunks):
            if task.cancel_event.is_set():
                raise asr.TranscriptError("已取消")
            try:
                buf: list[str] = []
                for delta in llm.polish_subtitle_stream(text, start, end):
                    buf.append(delta)
                    # 增量实时可见: worker append 与端点快照同锁, 边跑边读
                    with self._lock:
                        if task.id in self._tasks:
                            task.transcript_stream.append(delta)
                polished.extend(llm.parse_polished_lines("".join(buf), start, end))
            except (llm.LLMError, asr.TranscriptError):
                raise
            except Exception as e:  # 引擎异常统一为 LLM 错误: 调用方降级
                raise llm.LLMError(f"字幕重排异常: {e}") from e
            self.update_subtask(
                task.id,
                SUBTASK_TRANSCRIPT,
                ST_RUNNING,
                progress=min(80.0 + (idx + 1) / len(chunks) * 19.0, 99.0),
                message=f"字幕精修中 {idx + 1}/{len(chunks)}",
            )
        return polished

    def _wait_model_ready(self, task: Task) -> None:
        """等待模型就绪 (ADR-0006): 触发下载 (幂等) + 轮询进度映射 0~50.

        下载为全局资产: 幂等触发 (可能已被其他任务/手动触发), 已就绪即
        返回; 轮询期间转录子任务进度 = 模型下载进度 x 50% (0~50 区间,
        50 之后留给音频下载/转写), 任务取消抛 TranscriptError 退出 (下载
        线程独立于任务, 取消不中断). 下载失败 (状态回 missing) 抛错 → 转录
        failed, 用户可重试.
        """
        model_downloader.download()  # 幂等: 缺失启动线程 / 下载中 / 已就绪
        while not model_downloader.is_ready():
            if task.cancel_event.is_set():
                raise asr.TranscriptError("已取消")
            status = model_downloader.status()
            if status["status"] == model_downloader.STATUS_MISSING:
                # 触发后仍 missing = 下载线程已失败回退 (download() 同步置
                # downloading, 只有失败路径才回 missing)
                raise asr.TranscriptError("模型下载失败, 请稍后重试")
            self.update_subtask(
                task.id,
                SUBTASK_TRANSCRIPT,
                ST_RUNNING,
                progress=min(status["progress"] * 0.5, 49.9),
                message=f"模型下载中 {status['progress']:.0f}%",
            )
            time.sleep(0.5)

    def _refund_quota_on_cache_hit(self, task: Task) -> None:
        """字幕缓存命中退还配额 (ADR-0006): 创建时先扣, 命中净消耗 0.

        仅免费用户退还 (会员不限量无需退); quota_refunded 标志保证幂等
        (重试命中缓存不重复退还). 退还与转录结果解耦, 缓存命中与否由
        转录线程判定的语义一致.
        """
        if task.is_member or task.client_id is None or task.quota_refunded:
            return
        daily_quota.refund(task.client_id, QUOTA_KIND_SUMMARY)
        task.quota_refunded = True

    def _run_llm_subtask(self, task: Task, name: str) -> None:
        """LLM 生成子任务: summary 结构化总结 / mindmap 导图结构 (独立调用).

        summary 输入 = 转录文本: 流式收集, 增量实时写入 task.summary_stream
        (SSE 端点锁内快照轮询, ADR-0007), 流结束后统一解析结构化结果;
        mindmap 输入 = 结构化总结 (DAG 保证总结先完成, 用户反馈: 导图用
        总结后的数据). 置 progress=30 仅作「生成中」标记 (前端渲染不确定条),
        与旧 summary_progress 语义一致; 结果分别存 task.summary / task.mindmap.
        """
        self.update_subtask(
            task.id, name, ST_RUNNING, progress=30.0, message="正在生成"
        )
        meta = {"title": task.title, "duration": task.duration, "site": task.site}
        if name == SUBTASK_SUMMARY:
            # 重试/重跑前清空旧流缓冲 (防新旧文本拼接, ADR-0007)
            with self._lock:
                if task.id in self._tasks:
                    task.summary_stream.clear()
            chunks: list[str] = []
            for delta in llm.summarize_stream(
                segments_to_text(task.transcript or []), meta
            ):
                chunks.append(delta)
                # 增量实时可见: worker append 与端点快照同锁, 边跑边读
                with self._lock:
                    if task.id in self._tasks:
                        task.summary_stream.append(delta)
            # 文本是唯一事实源: 流结束后统一解析 (非法 JSON 抛 LLMError → failed)
            result = llm.parse_summary_text("".join(chunks))
        else:
            result = llm.generate_mindmap(task.summary or {}, meta)
        with self._lock:
            if task.id in self._tasks:
                if name == SUBTASK_SUMMARY:
                    task.summary = result
                else:
                    task.mindmap = result
        self.update_subtask(task.id, name, ST_DONE, progress=100.0, message="生成完成")

    def _finalize_subtasks(self, task_id: int) -> None:
        """DAG 结束后汇总任务终态: 全 done → completed; 有 done 有失败 →
        completed (部分完成, 失败子任务可重试); 全 failed/blocked → failed."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            subs = task.subtasks.values()
            all_done = all(s.status == ST_DONE for s in subs)
            any_done = any(s.status == ST_DONE for s in subs)
            if all_done:
                self.update_status(
                    task_id,
                    STATUS_COMPLETED,
                    progress=100.0,
                    message="总结完成",
                    completed_at=time.time(),  # TTL 起点: 结果就绪时刻 (与下载一致)
                )
            elif any_done:
                # 部分完成: 任务标记完成 (TTL 起点), 失败子任务经重试接口补齐
                self.update_status(
                    task_id,
                    STATUS_COMPLETED,
                    message="总结完成 (部分子任务失败, 可重试)",
                    completed_at=time.time(),
                )
            else:
                first_error = next((s.error for s in subs if s.error), "所有子任务失败")
                self.update_status(
                    task_id,
                    STATUS_FAILED,
                    error=f"总结失败: {first_error}",
                )

    def _transcript_progress(self, task_id: int, cancel_event: threading.Event):
        """ASR 阶段进度回调 → 转录子任务进度 (0~100, 四标签进度条数据源)."""

        def cb(stage: str, pct: float, msg: str) -> None:
            if cancel_event is not None and cancel_event.is_set():
                return  # 取消由 asr 内部检查中断, 此处仅停止上报
            # 阶段映射: 模型下载 0~50 → 音频下载 50~55 → 转写 55~80 →
            # 字幕精修 80~99 (重排由 _polish_segments 推进) → done 100
            progress = 50.0 + pct * 5.0 if stage == "audio" else 55.0 + pct * 25.0
            self.update_subtask(
                task_id,
                SUBTASK_TRANSCRIPT,
                ST_RUNNING,
                progress=min(progress, 79.9),
                message=msg,
            )

        return cb

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


def segments_to_text(segments: list[dict[str, Any]]) -> str:
    """转录段 → "[MM:SS] 文本" 组合文本 (LLM 输入 / 响应 / 导出共用)."""
    lines = []
    for seg in segments:
        mm, ss = divmod(int(seg["start"]), 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {seg['text']}")
    return "\n".join(lines)


def _split_polish_chunks(
    segments: list[dict[str, Any]], max_chars: int
) -> list[tuple[float, float, str]]:
    """字幕段按累计字符切块 → [(start, end, 拼接文本)] (块范围 = 首/末句时间).

    空文本段跳过 (ASR 清洗后可能残留空串); 拼接以换行分隔, 保留断句结构
    供 LLM 参考. 块的时间范围随块内容走, 重排输出的时间戳均匀分布其内.
    """
    chunks: list[tuple[float, float, str]] = []
    buf: list[dict[str, Any]] = []
    count = 0
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if buf and count + len(text) > max_chars:
            chunks.append(
                (buf[0]["start"], buf[-1]["end"], "\n".join(s["text"] for s in buf))
            )
            buf, count = [], 0
        buf.append(seg)
        count += len(text)
    if buf:
        chunks.append(
            (buf[0]["start"], buf[-1]["end"], "\n".join(s["text"] for s in buf))
        )
    return chunks


# 模块级单例: 路由与测试共享同一任务存储
manager = TaskManager()
