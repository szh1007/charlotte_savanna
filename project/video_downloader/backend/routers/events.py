"""GET /api/events: SSE 进度流 (T03).

事件协议 (PRD §8): event: task-update, data 为 JSON
{task_id, status, progress, message, url?, error?}; 空闲连接 15s 心跳保活.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..events import bus, model_update_event, task_event
from ..model_downloader import model_downloader
from ..task_manager import manager

router = APIRouter(tags=["events"])

# 心跳间隔 (秒): 无事件时每 15s 推送 heartbeat 保活 (PRD 设计值)
HEARTBEAT_INTERVAL = 15.0


def _sse_frame(event: dict) -> str:
    """单事件帧: event + data (JSON, 中文不转义), 空行结尾.

    event 名为事件负载的 "event" 键 (task-update / model-update, ADR-0006),
    不硬编码 — 模型进度是全局资产事件, 无事件名标记的旧负载缺省 task-update.
    """
    name = event.get("event", "task-update")
    return f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _heartbeat_frame() -> str:
    """心跳帧: 空闲连接保活 (event: heartbeat)."""
    return "event: heartbeat\ndata: {}\n\n"


@router.get("/api/events")
async def task_events(
    request: Request,
    task_ids: str | None = Query(default=None, description="关注的任务 id, 逗号分隔"),
) -> StreamingResponse:
    """SSE 流: 连接建立先推当前快照, 之后持续推送任务状态事件与心跳."""
    wanted: set[int] | None = None
    if task_ids:
        wanted = {int(part) for part in task_ids.split(",") if part.strip().isdigit()}
        if not wanted:
            raise HTTPException(
                status_code=422, detail="task_ids 必须为逗号分隔的整数列表"
            )

    sub = bus.subscribe(loop=asyncio.get_running_loop(), task_ids=wanted)

    async def event_stream() -> AsyncIterator[str]:
        try:
            # 初始快照: 断线重连后恢复现场 (只推订阅者关注的任务)
            for task in manager.list_tasks():
                if sub.accepts(task.id):
                    yield _sse_frame(task_event(task))
            # 模型状态快照 (ADR-0006): 全局资产, 连接建立即推当前状态
            # (前端由此恢复下载按钮/进度条显示, 不依赖事件时序)
            st = model_downloader.status()
            yield _sse_frame(model_update_event(st["status"], st["progress"]))
            while True:
                try:
                    event = await asyncio.wait_for(
                        sub.queue.get(), timeout=HEARTBEAT_INTERVAL
                    )
                except TimeoutError:
                    # 空闲保活: 同时兼断连探测 (断开后下一次 send 失败,
                    # 生成器结束走 finally 清理订阅)
                    yield _heartbeat_frame()
                    continue
                yield _sse_frame(event)
        finally:
            # 客户端断开 / 服务端异常统一在此清理订阅 (send 失败或心跳均触发)
            bus.unsubscribe(sub)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
