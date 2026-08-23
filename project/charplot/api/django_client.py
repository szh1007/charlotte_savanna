"""FastAPI → Django 内部端点客户端 (Issue 03).

双后端写记录必经 Django API (DESIGN.md §2); 认证用共享
X-Internal-Token (CONTRACT.md §3). 落库失败语义: transient 错误重试 1 次,
4xx (契约校验失败) 直接抛, 由任务系统转 error 事件.
"""

import asyncio
import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


async def _post_internal(path: str, payload: dict) -> httpx.Response:
    headers = {"X-Internal-Token": config.INTERNAL_TOKEN}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        return await client.post(
            f"{config.DJANGO_API_BASE}{path}", json=payload, headers=headers
        )


async def save_graph_to_django(journey_id: int, task_id: str, graph: dict) -> None:
    """图谱落库, transient 失败重试 1 次 (间隔 1s); 4xx 抛异常不重试."""
    path = f"/api/charplot/journeys/{journey_id}/graph/"
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = await _post_internal(path, {"task_id": task_id, "graph": graph})
        except httpx.HTTPError as exc:
            last_exc = exc
            await asyncio.sleep(1.0)
            continue
        if 400 <= resp.status_code < 500:
            raise RuntimeError(
                f"图谱落库被拒绝 ({resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code >= 500:
            last_exc = RuntimeError(f"Django 服务错误 ({resp.status_code})")
            await asyncio.sleep(1.0)
            continue
        if resp.status_code == 200:
            return
        last_exc = RuntimeError(f"意外状态码 {resp.status_code}")
    raise RuntimeError(f"图谱落库失败: {last_exc}")


async def mark_journey_failed(
    journey_id: int, task_id: str, error_message: str
) -> None:
    """任务失败标记 (best-effort): Django 不可达时静默, 前端靠 SSE error 事件兜底."""
    try:
        path = f"/api/charplot/journeys/{journey_id}/status/"
        await _post_internal(
            path, {"task_id": task_id, "error_message": error_message[:1000]}
        )
    except Exception as exc:
        logger.warning("mark_journey_failed 调用失败 (journey=%s): %s", journey_id, exc)
