"""CharPlot FastAPI 服务 - AI 能力端.

职责 (ADR-0001): 知识管道 / RAG 全链路 / 任务系统 (Issue 03: stub 管道 +
异步任务 + SSE 进度).
Django 侧 = 状态与数据 (app/charplot), FastAPI 侧 = AI 能力, 二者通过
HTTP + 共享 MySQL/Redis 通信.
"""

import logging
import os
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from redis.asyncio import Redis

from ..pipeline import llm
from ..prompt.status_summary import (
    STATUS_SUMMARY_SYSTEM_PROMPT,
    build_status_summary_prompt,
)

# .env 加载由 api/config.py 模块顶部完成 (首个被 import 的配置模块), 本模块不重复
from . import tasks as task_system
from .django_client import (
    UserNotFoundError,
    fetch_status_summary_input,
)
from .schemas import (
    KbIndexRequest,
    KbIndexResponse,
    KbSearchChunk,
    KbSearchRequest,
    KbSearchResponse,
    LevelGenerateRequest,
    LevelGenerateResponse,
    PipelineRequest,
    PipelineResponse,
    StatusSummaryRequest,
    StatusSummaryResponse,
    TaskStatusOut,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="CharPlot AI Service", version="0.1.0")

# 前端开发服务器跨域访问 (Vite dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:9004",
        "http://127.0.0.1:9004",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ai/health")
async def health():
    """健康检查 - 探活共享 Redis.

    三端联通的基础链路 (Issue 01): 前端可轮询此端点确认 AI 服务就绪.
    """
    redis_status = "ok"
    try:
        client = Redis.from_url(
            os.getenv("CHARPLOT_REDIS_URL", "redis://127.0.0.1:6379/4"),
            decode_responses=True,
        )
        await client.ping()
        await client.aclose()
    except Exception:
        redis_status = "error"

    payload = {
        "status": "ok" if redis_status == "ok" else "degraded",
        "service": "charplot-fastapi",
        "redis": redis_status,
        "time": datetime.now().isoformat(),
    }
    return payload


@app.post("/ai/report/summary", response_model=StatusSummaryResponse)
async def status_summary(req: StatusSummaryRequest):
    """LLM 状态总结 (Issue 13, DESIGN.md §4.2 步骤 13): 聚合 → 文字报告.

    同步调用 (单次 LLM 生成, 不落库, 可重复生成; 前端加载态 + 失败重试).
    聚合事实经内部端点从 Django 侧权威获取 (按 user_id 隔离). 错误语义:
    用户不存在 → 404; 取聚合数据失败 → 502; LLM 未配置 → 503;
    LLM 调用失败 → 502 (均可在修复后重试).
    """
    try:
        aggregate = await fetch_status_summary_input(req.user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        model = llm.get_chat_model()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        resp = await model.ainvoke(
            [
                SystemMessage(content=STATUS_SUMMARY_SYSTEM_PROMPT),
                HumanMessage(content=build_status_summary_prompt(aggregate)),
            ]
        )
    except Exception as exc:
        logger.exception("状态总结失败 (user=%s)", req.user_id)
        raise HTTPException(status_code=502, detail=f"生成状态总结失败: {exc}") from exc
    return StatusSummaryResponse(summary=str(resp.content))


@app.post("/ai/pipeline", response_model=PipelineResponse)
async def start_pipeline(req: PipelineRequest):
    """启动知识管道 (DESIGN §4.2): 创建异步任务, 后台执行 stub 管道."""
    task_id = await task_system.create_task(
        req.journey_id, req.input_type, req.content or "", req.kb_id
    )
    return PipelineResponse(task_id=task_id)


@app.post("/ai/levels/generate", response_model=LevelGenerateResponse)
async def generate_level_questions(req: LevelGenerateRequest):
    """渐进出题 (DESIGN §4.2): 创建出题异步任务, 后台抢占 + LLM 生成 + 落库.

    claimed=False 表示关卡已就绪或已有任务在跑 (幂等跳过), 前端可直接刷新.
    """
    task_id = await task_system.create_level_generation_task(
        req.journey_id, req.level_seq
    )
    return LevelGenerateResponse(task_id=task_id)


@app.post("/ai/kb/index", response_model=KbIndexResponse)
async def start_kb_index(req: KbIndexRequest):
    """知识库索引任务 (DESIGN §4.2 POST /ai/kb/index): 创建异步任务.

    真实索引 (Issue 10): 解析 → 切分 → embedding → Milvus 入库, SSE
    阶段进度可见; 幂等/拒绝理由由 Django 侧 claim 保证 (索引中/下线/
    无文档 → 任务直接 done 跳过, 前端可刷新).
    """
    task_id = await task_system.create_kb_index_task(req.kb_id)
    return KbIndexResponse(task_id=task_id)


@app.post("/ai/kb/search", response_model=KbSearchResponse)
async def search_kb(req: KbSearchRequest):
    """混合检索 + rerank (DESIGN §4.2, QA.md Q7): 片段检索, 不是答案.

    全链路: query rewriting → 稠密+稀疏混合检索 (软删 filter 排除,
    立即生效) → rerank 精排 → Top-K 片段. 供管道 A 解构 / C 出题
    (Issue 11) 与调试调用.
    """
    from ..rag.retriever import search_kb as rag_search

    try:
        # 同步调用与 pipeline 内 LLM 调用同款 (单机自用项目, 不引入线程池)
        chunks = rag_search(req.kb_id, req.query, req.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KbSearchResponse(chunks=[KbSearchChunk(**chunk) for chunk in chunks])


@app.get("/ai/tasks/{task_id}", response_model=TaskStatusOut)
async def get_task(task_id: str):
    """任务状态 (DESIGN §4.2). 任务不存在 (过期/服务重启) → 404."""
    task = await task_system.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return TaskStatusOut(**task)


@app.get("/ai/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request):
    """SSE 进度流 (DESIGN §4.2): event 名 pipeline-progress, 帧带递增 id.

    断线重连客户端自动携带 Last-Event-ID, 服务端从增量续推 (CONTRACT.md §2).
    """
    task = await task_system.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    last_event_id = request.headers.get("last-event-id", "")
    start_after = int(last_event_id) if last_event_id.isdigit() else -1
    return StreamingResponse(
        task_system.event_stream(task_id, start_after=start_after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    # 独立启动: python -m project.charplot.api.server
    uvicorn.run(app, host="127.0.0.1", port=8004)
