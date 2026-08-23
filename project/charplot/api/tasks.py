"""CharPlot 任务系统 (Issue 03, CONTEXT Q14): FastAPI 异步任务 + Redis 状态 + SSE.

Redis 数据结构 (db 0, charplot:task: 前缀):
  charplot:task:{task_id}             HASH  {status, stage, progress, ...}
  charplot:task:{task_id}:events      LIST  事件 JSON 串, 下标即事件序号 (SSE id 帧)
两个 key 均 EXPIRE 24h (任务持久化明确不做, DESIGN.md §8).

SSE 恢复: 客户端断线重连带 Last-Event-ID (= 最后收到的序号), 服务端从
LIST 增量续推, 不丢事件; 服务重启丢内存任务 → GET events 404 → 前端兜底
「重新生成」(CONTRACT.md §2).
"""

import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import partial

from redis.asyncio import Redis

from ..pipeline import PipelineInput, run_pipeline
from .django_client import mark_journey_failed, save_graph_to_django

logger = logging.getLogger(__name__)

TASK_TTL_SECONDS = 86400  # 任务状态与事件 24h 过期
EVENT_POLL_INTERVAL = 0.5  # SSE 轮询间隔 (秒)
TERMINAL_STAGES = {"done", "error"}

_redis: Redis | None = None
_tasks_registry: dict[str, asyncio.Task] = {}  # 内存引用保活, 防 GC 中断任务


def get_redis() -> Redis:
    """模块级惰性 Redis 单例 (decode_responses=True)."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True,
        )
    return _redis


def _task_key(task_id: str) -> str:
    return f"charplot:task:{task_id}"


def _events_key(task_id: str) -> str:
    return f"charplot:task:{task_id}:events"


async def create_task(journey_id: int, input_type: str, content: str) -> str:
    """初始化任务 hash 并后台执行管道, 返回 task_id."""
    redis = get_redis()
    task_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    async with redis.pipeline() as pipe:
        pipe.hset(
            _task_key(task_id),
            mapping={
                "status": "running",
                "stage": "parsing",
                "progress": 0,
                "journey_id": str(journey_id),
                "error_message": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        pipe.expire(_task_key(task_id), TASK_TTL_SECONDS)
        pipe.expire(_events_key(task_id), TASK_TTL_SECONDS)
        await pipe.execute()
    _tasks_registry[task_id] = asyncio.create_task(
        _run_task(task_id, journey_id, input_type, content)
    )
    return task_id


async def get_task(task_id: str) -> dict | None:
    """任务状态 (TaskStatusOut 同构), hash 不存在 (任务过期/服务重启) 返回 None."""
    data = await get_redis().hgetall(_task_key(task_id))
    if not data:
        return None
    return {
        "task_id": task_id,
        "status": data.get("status", "running"),
        "stage": data.get("stage", ""),
        "progress": int(data.get("progress", 0)),
        "error_message": data.get("error_message") or None,
    }


async def emit(task_id: str, stage: str, progress: int, message: str) -> None:
    """阶段事件: 更新 hash + 追加事件 LIST (RPUSH, 下标即序号)."""
    redis = get_redis()
    payload = {
        "task_id": task_id,
        "stage": stage,
        "progress": progress,
        "message": message,
    }
    await redis.hset(
        _task_key(task_id),
        mapping={
            "status": "running",
            "stage": stage,
            "progress": str(progress),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    await redis.rpush(_events_key(task_id), json.dumps(payload, ensure_ascii=False))


async def _run_task(
    task_id: str, journey_id: int, input_type: str, content: str
) -> None:
    """任务执行体: 管道 → 图谱落库 → done; 任何失败 → error + 失败标记."""
    redis = get_redis()
    last_progress = 0
    try:
        graph = await run_pipeline(
            PipelineInput(
                journey_id=journey_id, input_type=input_type, content=content or ""
            ),
            partial(emit, task_id),
        )
        # 落库写自动重试 1 次在 django_client 内部 (transient), 失败抛异常
        await save_graph_to_django(journey_id, task_id, graph)
        await emit(task_id, "done", 100, "完成, 图谱已保存")
        await redis.hset(_task_key(task_id), mapping={"status": "done"})
    except Exception as exc:
        logger.exception("任务 %s 失败 (journey=%s)", task_id, journey_id)
        await emit(task_id, "error", last_progress, f"生成失败: {exc}")
        await redis.hset(
            _task_key(task_id),
            mapping={"status": "error", "error_message": str(exc)[:1000]},
        )
        with contextlib.suppress(Exception):
            # mark 自身已兜底, 此处防御
            await mark_journey_failed(journey_id, task_id, str(exc))
    finally:
        _tasks_registry.pop(task_id, None)


async def event_stream(task_id: str, start_after: int = -1) -> AsyncIterator[str]:
    """SSE 帧流: 全量/增量重放事件 LIST, 终端事件 (done/error) 后流结束.

    start_after = Last-Event-ID (重连时从增量续推; -1 = 全量重放).
    """
    redis = get_redis()
    last = start_after
    while True:
        raw_events = await redis.lrange(_events_key(task_id), last + 1, -1)
        for idx, raw in enumerate(raw_events, start=last + 1):
            yield (f"id: {idx}\nevent: pipeline-progress\ndata: {raw}\n\n")
            last = idx
            if json.loads(raw)["stage"] in TERMINAL_STAGES:
                return
        await asyncio.sleep(EVENT_POLL_INTERVAL)
