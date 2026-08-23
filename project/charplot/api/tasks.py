"""CharPlot 任务系统 (Issue 03/08, CONTEXT Q14): FastAPI 异步任务 + Redis 状态 + SSE.

Redis 数据结构 (db 0, charplot:task: 前缀):
  charplot:task:{task_id}             HASH  {status, stage, progress, task_type, ...}
  charplot:task:{task_id}:events      LIST  事件 JSON 串, 下标即事件序号 (SSE id 帧)
两个 key 均 EXPIRE 24h (任务持久化明确不做, DESIGN.md §8).

任务类型 (task_type): pipeline = 知识管道 (Issue 03); level-generation =
渐进出题 (Issue 08, DESIGN §4.2). 出题任务 stages: preparing → generating
→ saving → done/error, SSE 事件名统一 pipeline-progress (DESIGN §4.2 先例:
不同任务类型不同 stage 列表, 同一事件名).

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
from ..pipeline.parsers import parse_document
from ..pipeline.questions import generate_level_questions
from .django_client import (
    claim_kb_index,
    claim_level_generation,
    fetch_kb_document_content,
    mark_journey_failed,
    mark_kb_index_failed,
    mark_level_generation_failed,
    save_graph_to_django,
    save_kb_index_success,
    save_level_questions,
)

logger = logging.getLogger(__name__)

TASK_TTL_SECONDS = 86400  # 任务状态与事件 24h 过期
EVENT_POLL_INTERVAL = 0.5  # SSE 轮询间隔 (秒)
TERMINAL_STAGES = {"done", "error"}

TASK_TYPE_PIPELINE = "pipeline"
TASK_TYPE_LEVEL_GENERATION = "level-generation"
TASK_TYPE_KB_INDEX = "kb-index"

_redis: Redis | None = None
_tasks_registry: dict[str, asyncio.Task] = {}  # 内存引用保活, 防 GC 中断任务


def get_redis() -> Redis:
    """模块级惰性 Redis 单例 (decode_responses=True).

    socket_timeout=None: redis-py 8 默认 5s 读超时, 但任务执行体内的
    同步 AI 操作 (文档解析/embedding) 会阻塞事件循环, 偶发阻塞超 5s
    导致状态写被误杀 (TimeoutError); 本地状态存储显式恢复无超时语义.
    """
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True,
            socket_timeout=None,
        )
    return _redis


def _task_key(task_id: str) -> str:
    return f"charplot:task:{task_id}"


def _events_key(task_id: str) -> str:
    return f"charplot:task:{task_id}:events"


async def _init_task(
    task_id: str,
    entity_id: int,
    task_type: str,
    stage: str,
    entity_type: str = "journey",
) -> None:
    """初始化任务 hash (hset + EXPIRE), 内存 registry 由调用方注册执行体.

    Issue 09: entity_type 泛化 (journey / kb), hash 增写 entity_id/entity_type
    两键; 旧键 journey_id 保留 (get_task 与测试不读该键, 向后兼容).
    """
    redis = get_redis()
    now = datetime.now(UTC).isoformat()
    async with redis.pipeline() as pipe:
        pipe.hset(
            _task_key(task_id),
            mapping={
                "status": "running",
                "stage": stage,
                "progress": 0,
                "journey_id": str(entity_id),
                "entity_id": str(entity_id),
                "entity_type": entity_type,
                "task_type": task_type,
                "error_message": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        pipe.expire(_task_key(task_id), TASK_TTL_SECONDS)
        pipe.expire(_events_key(task_id), TASK_TTL_SECONDS)
        await pipe.execute()


async def create_task(journey_id: int, input_type: str, content: str) -> str:
    """初始化管道任务 hash 并后台执行, 返回 task_id."""
    task_id = uuid.uuid4().hex
    await _init_task(task_id, journey_id, TASK_TYPE_PIPELINE, "parsing")
    _tasks_registry[task_id] = asyncio.create_task(
        _run_task(task_id, journey_id, input_type, content)
    )
    return task_id


async def create_level_generation_task(journey_id: int, level_seq: int) -> str:
    """初始化出题任务 hash 并后台执行 (DESIGN §4.2 /ai/levels/generate)."""
    task_id = uuid.uuid4().hex
    await _init_task(task_id, journey_id, TASK_TYPE_LEVEL_GENERATION, "preparing")
    _tasks_registry[task_id] = asyncio.create_task(
        _run_level_generation_task(task_id, journey_id, level_seq)
    )
    return task_id


async def create_kb_index_task(kb_id: int) -> str:
    """初始化知识库索引任务 hash 并后台执行 (DESIGN §4.2 /ai/kb/index).

    task_type=kb-index, 阶段 parsing → chunking → embedding → indexing
    → done/error (CONTRACT.md §6.5); 幂等/拒绝理由由 Django 侧 claim 保证.
    """
    task_id = uuid.uuid4().hex
    await _init_task(task_id, kb_id, TASK_TYPE_KB_INDEX, "parsing", entity_type="kb")
    _tasks_registry[task_id] = asyncio.create_task(_run_kb_index_task(task_id, kb_id))
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
        "task_type": data.get("task_type", TASK_TYPE_PIPELINE),
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


async def _run_level_generation_task(
    task_id: str, journey_id: int, level_seq: int
) -> None:
    """出题任务执行体 (Issue 08): 抢占 → LLM 生成 → 落库 → done.

    抢占未成功 (关卡已就绪/已有任务在跑) → 直接 done, 幂等由 Django 侧
    claim 保证; 生成失败 → error + mark_level_generation_failed (best-effort),
    前端靠 SSE error 事件刷新关卡列表并展示「生成失败 · 重试」.
    """
    redis = get_redis()
    try:
        await emit(task_id, "preparing", 10, "准备出题素材")
        claimed, payload = await claim_level_generation(journey_id, level_seq, task_id)
        if not claimed:
            reason = payload.get("reason", "unknown")
            await emit(task_id, "done", 100, f"关卡已就绪或生成中 ({reason}), 跳过")
            await redis.hset(_task_key(task_id), mapping={"status": "done"})
            return
        await emit(task_id, "generating", 60, "生成闯关题目")
        new_questions = await generate_level_questions(payload["input"])
        # 间隔复习题由 Django 透传 (含完整答案), 固定置于新题末尾
        final = [*new_questions, *payload["input"]["review_questions"]]
        await emit(task_id, "saving", 90, "保存题目")
        await save_level_questions(journey_id, level_seq, task_id, final)
        await emit(task_id, "done", 100, "完成, 题目已生成")
        await redis.hset(_task_key(task_id), mapping={"status": "done"})
    except Exception as exc:
        logger.exception("出题任务 %s 失败 (journey=%s)", task_id, journey_id)
        await emit(task_id, "error", 0, f"题目生成失败: {exc}")
        await redis.hset(
            _task_key(task_id),
            mapping={"status": "error", "error_message": str(exc)[:1000]},
        )
        with contextlib.suppress(Exception):
            # mark 自身已兜底, 此处防御
            await mark_level_generation_failed(journey_id, level_seq, task_id, str(exc))
    finally:
        _tasks_registry.pop(task_id, None)


async def _run_kb_index_task(task_id: str, kb_id: int) -> None:
    """索引任务执行体 (Issue 10, 真实索引): 抢占 → per-doc 解析/切分/向量化
    → Milvus 全量重建入库 → 落库 → done.

    全链路 (SPEC §7.2): 文档二进制经 Django 内部端点获取 (CONTRACT §6.6),
    解析复用 pipeline.parsers (pdf/docx/pptx/md/txt/html) → 按类型调优
    切分 (rag.chunking) → embedding 抽象 (rag.embeddings, 可切换) →
    Milvus drop+create 全量重建 (rag.milvus.ensure_collection, 软删物理
    剔除) + 批量入库. 每文档两阶段事件 (chunking/embedding) 提供真实
    进度 (契约阶段序列不变, CONTRACT §6.5).

    抢占未成功 (索引中/下线/无文档) → 直接 done, 幂等由 Django 侧 claim
    保证; 任一文档失败 (取内容/解析/向量化) → error + mark_kb_index_failed
    (best-effort), 前端靠 SSE error 事件刷新并展示「失败 · 重试」.
    """
    redis = get_redis()
    try:
        await emit(task_id, "parsing", 15, "解析文档")
        claimed, payload = await claim_kb_index(kb_id, task_id)
        if not claimed:
            reason = payload.get("reason", "unknown")
            await emit(task_id, "done", 100, f"索引跳过 ({reason})")
            await redis.hset(_task_key(task_id), mapping={"status": "done"})
            return
        documents = payload.get("documents", [])
        total = len(documents)
        # per-doc 流水线: 解析+切分 → 向量化 → 暂存行, 进度 40→85 单调递增
        from ..rag.chunking import split_document
        from ..rag.embeddings import get_embedder

        embedder = get_embedder()
        total_steps = total * 2
        all_rows: list[dict] = []
        for idx, doc in enumerate(documents, start=1):
            doc_id = int(doc["id"])
            filename, raw = await fetch_kb_document_content(doc_id)
            text = parse_document(filename, raw)
            chunks = split_document(
                text,
                doc_id=doc_id,
                title=doc.get("title") or filename,
                filename=filename,
                extension=doc.get("extension", ""),
            )
            await emit(
                task_id,
                "chunking",
                40 + int(45 * (2 * idx - 1) / total_steps),
                f"切分文档 {idx}/{total}: {filename}",
            )
            if chunks:
                vectors = embedder.embed_documents([c["content"] for c in chunks])
                for chunk, dense, sparse in zip(
                    chunks, vectors["dense"], vectors["sparse"]
                ):
                    all_rows.append(
                        {
                            "id": f"{doc_id}-{chunk['chunk_index']}",
                            "kb_id": kb_id,
                            "doc_id": doc_id,
                            "title": chunk["title"],
                            "filename": chunk["filename"],
                            "chunk_index": chunk["chunk_index"],
                            "valid": chunk["valid"],
                            "content": chunk["content"],
                            "dense_vector": dense,
                            "sparse_vector": sparse,
                        }
                    )
            await emit(
                task_id,
                "embedding",
                40 + int(45 * (2 * idx) / total_steps),
                f"向量化 {idx}/{total}: {filename} ({len(chunks)} chunks)",
            )
        # 全量重建入库: drop + create (软删物理剔除) + 批量写入
        from ..rag.milvus import ensure_collection, insert_chunks
        from ..rag.retriever import collection_name

        await emit(task_id, "indexing", 90, f"写入向量库 ({len(all_rows)} chunks)")
        ensure_collection(collection_name(kb_id))
        insert_chunks(collection_name(kb_id), all_rows)
        await save_kb_index_success(kb_id, task_id)
        await emit(task_id, "done", 100, "完成, 知识库已就绪")
        await redis.hset(_task_key(task_id), mapping={"status": "done"})
    except Exception as exc:
        logger.exception("索引任务 %s 失败 (kb=%s)", task_id, kb_id)
        await emit(task_id, "error", 0, f"索引失败: {exc}")
        await redis.hset(
            _task_key(task_id),
            mapping={"status": "error", "error_message": str(exc)[:1000]},
        )
        with contextlib.suppress(Exception):
            # mark 自身已兜底, 此处防御
            await mark_kb_index_failed(kb_id, task_id, str(exc))
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
