"""CharPlot FastAPI 服务 - AI 能力端.

职责 (ADR-0001): 知识管道 / RAG 全链路 / 任务系统 (Issue 03: stub 管道 +
异步任务 + SSE 进度).
Django 侧 = 状态与数据 (app/charplot), FastAPI 侧 = AI 能力, 二者通过
HTTP + 共享 MySQL/Redis 通信.
"""

import os
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

# .env 加载由 api/config.py 模块顶部完成 (首个被 import 的配置模块), 本模块不重复
from . import tasks as task_system
from .schemas import PipelineRequest, PipelineResponse, TaskStatusOut

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
            os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
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


@app.post("/ai/pipeline", response_model=PipelineResponse)
async def start_pipeline(req: PipelineRequest):
    """启动知识管道 (DESIGN §4.2): 创建异步任务, 后台执行 stub 管道."""
    task_id = await task_system.create_task(
        req.journey_id, req.input_type, req.content or ""
    )
    return PipelineResponse(task_id=task_id)


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
