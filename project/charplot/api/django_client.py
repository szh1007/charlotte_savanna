"""FastAPI → Django 内部端点客户端 (Issue 03).

双后端写记录必经 Django API (DESIGN.md §2); 认证用共享
X-Internal-Token (CONTRACT.md §3). 落库失败语义: transient 错误重试 1 次,
4xx (契约校验失败) 直接抛, 由任务系统转 error 事件.
"""

import asyncio
import base64
import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


def _internal_headers() -> dict:
    return {"X-Internal-Token": config.INTERNAL_TOKEN}


async def _post_internal(path: str, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        return await client.post(
            f"{config.DJANGO_API_BASE}{path}",
            json=payload,
            headers=_internal_headers(),
        )


async def fetch_journey_content(journey_id: int) -> tuple[str, bytes]:
    """取 file 输入的源文件二进制 (内部端点, Issue 07).

    返回 (filename, bytes); 4xx (契约/无文件) 与网络错误抛 RuntimeError,
    由管道转任务 error (前端可重试). 解析在 FastAPI 侧完成.
    """
    path = f"/api/charplot/journeys/{journey_id}/content/"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{config.DJANGO_API_BASE}{path}", headers=_internal_headers()
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"取源文件内容失败 (网络): {exc}") from exc
    if resp.status_code != 200:
        detail = resp.text[:200] if resp.text else resp.status_code
        raise RuntimeError(f"取源文件内容失败 ({resp.status_code}): {detail}")
    body = resp.json()
    try:
        raw = base64.b64decode(body["content_base64"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"源文件内容响应异常: {exc}") from exc
    return body.get("filename", "upload"), raw


async def fetch_kb_meta(kb_id: int) -> dict:
    """取知识库元信息 (内部端点, Issue 11 kb 旅程 parse 输入).

    返回 {id, name, description, status}; 4xx (不存在) 与网络错误抛
    RuntimeError, 由管道转任务 error (前端可重试). status 供管道运行时
    快速失败 (知识库创建后被下线/删除的竞态).
    """
    path = f"/api/charplot/kb/{kb_id}/meta/"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{config.DJANGO_API_BASE}{path}", headers=_internal_headers()
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"取知识库元信息失败 (网络): {exc}") from exc
    if resp.status_code != 200:
        detail = resp.text[:200] if resp.text else resp.status_code
        raise RuntimeError(f"取知识库元信息失败 ({resp.status_code}): {detail}")
    return resp.json()


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


async def claim_level_generation(
    journey_id: int, level_seq: int, task_id: str
) -> tuple[bool, dict]:
    """出题任务抢占 (Issue 08, 内部端点): 原子置 generating 并取回出题输入.

    返回 (claimed, payload): claimed=True 时 payload["input"] 为出题素材
    (含间隔复习题, 带完整答案); claimed=False 时 payload 为
    {"reason": "ready"|"generating", "task_id": 现有} (幂等跳过).
    """
    path = f"/api/charplot/journeys/{journey_id}/level-generation/"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{config.DJANGO_API_BASE}{path}",
                json={"task_id": task_id, "level_seq": level_seq},
                headers=_internal_headers(),
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"出题抢占失败 (网络): {exc}") from exc
    if resp.status_code not in (200,):
        detail = resp.text[:200] if resp.text else resp.status_code
        raise RuntimeError(f"出题抢占失败 ({resp.status_code}): {detail}")
    body = resp.json()
    claimed = bool(body.get("claimed"))
    return claimed, body


async def save_level_questions(
    journey_id: int, level_seq: int, task_id: str, questions: list[dict]
) -> None:
    """题目落库, transient 失败重试 1 次 (间隔 1s); 4xx 抛异常不重试."""
    path = f"/api/charplot/journeys/{journey_id}/level-generation/questions/"
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = await _post_internal(
                path,
                {"task_id": task_id, "level_seq": level_seq, "questions": questions},
            )
        except httpx.HTTPError as exc:
            last_exc = exc
            await asyncio.sleep(1.0)
            continue
        if 400 <= resp.status_code < 500:
            raise RuntimeError(
                f"题目落库被拒绝 ({resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code >= 500:
            last_exc = RuntimeError(f"Django 服务错误 ({resp.status_code})")
            await asyncio.sleep(1.0)
            continue
        if resp.status_code == 200:
            return
        last_exc = RuntimeError(f"意外状态码 {resp.status_code}")
    raise RuntimeError(f"题目落库失败: {last_exc}")


async def mark_level_generation_failed(
    journey_id: int, level_seq: int, task_id: str, error_message: str
) -> None:
    """出题失败标记 (best-effort): Django 不可达时静默, 前端靠 SSE error 兜底."""
    try:
        path = f"/api/charplot/journeys/{journey_id}/level-generation/failed/"
        await _post_internal(
            path,
            {
                "task_id": task_id,
                "level_seq": level_seq,
                "error_message": error_message[:1000],
            },
        )
    except Exception as exc:
        logger.warning(
            "mark_level_generation_failed 调用失败 (journey=%s, seq=%s): %s",
            journey_id,
            level_seq,
            exc,
        )


async def claim_kb_index(kb_id: int, task_id: str) -> tuple[bool, dict]:
    """索引任务抢占 (Issue 09, 内部端点): 原子置 indexing 并取回文档清单.

    返回 (claimed, payload): claimed=True 时 payload["documents"] 为有效
    文档清单 (id/title/filename/file_size/extension, Issue 10 索引输入);
    claimed=False 时 payload 为 {"reason": "indexing"|"offline"|
    "no_documents", "task_id"?} (幂等跳过).
    """
    path = f"/api/charplot/kb/{kb_id}/index-claim/"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{config.DJANGO_API_BASE}{path}",
                json={"task_id": task_id},
                headers=_internal_headers(),
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"索引抢占失败 (网络): {exc}") from exc
    if resp.status_code != 200:
        detail = resp.text[:200] if resp.text else resp.status_code
        raise RuntimeError(f"索引抢占失败 ({resp.status_code}): {detail}")
    body = resp.json()
    claimed = bool(body.get("claimed"))
    return claimed, body


async def save_kb_index_success(kb_id: int, task_id: str) -> None:
    """索引完成落库, transient 失败重试 1 次 (间隔 1s); 4xx 抛异常不重试."""
    path = f"/api/charplot/kb/{kb_id}/index-save/"
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = await _post_internal(path, {"task_id": task_id})
        except httpx.HTTPError as exc:
            last_exc = exc
            await asyncio.sleep(1.0)
            continue
        if 400 <= resp.status_code < 500:
            raise RuntimeError(
                f"索引完成落库被拒绝 ({resp.status_code}): {resp.text[:200]}"
            )
        if resp.status_code >= 500:
            last_exc = RuntimeError(f"Django 服务错误 ({resp.status_code})")
            await asyncio.sleep(1.0)
            continue
        if resp.status_code == 200:
            return
        last_exc = RuntimeError(f"意外状态码 {resp.status_code}")
    raise RuntimeError(f"索引完成落库失败: {last_exc}")


async def mark_kb_index_failed(kb_id: int, task_id: str, error_message: str) -> None:
    """索引失败标记 (best-effort): Django 不可达时静默, 前端靠 SSE error 兜底."""
    try:
        path = f"/api/charplot/kb/{kb_id}/index-failed/"
        await _post_internal(
            path, {"task_id": task_id, "error_message": error_message[:1000]}
        )
    except Exception as exc:
        logger.warning("mark_kb_index_failed 调用失败 (kb=%s): %s", kb_id, exc)


async def fetch_kb_document_content(doc_id: int) -> tuple[str, bytes]:
    """取知识库文档文件二进制 (内部端点, CONTRACT.md §6.6, Issue 10).

    返回 (filename, bytes); 4xx (契约/无文件) 与网络错误抛 RuntimeError,
    由索引任务转 error (前端可重试). 与 fetch_journey_content 同构,
    解析在 FastAPI 侧完成.
    """
    path = f"/api/charplot/kb/documents/{doc_id}/content/"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{config.DJANGO_API_BASE}{path}", headers=_internal_headers()
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"取知识库文档内容失败 (网络): {exc}") from exc
    if resp.status_code != 200:
        detail = resp.text[:200] if resp.text else resp.status_code
        raise RuntimeError(f"取知识库文档内容失败 ({resp.status_code}): {detail}")
    body = resp.json()
    try:
        raw = base64.b64decode(body["content_base64"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"知识库文档内容响应异常: {exc}") from exc
    return body.get("filename", "document"), raw


def fetch_kb_deleted_doc_ids(kb_id: int) -> list[int]:
    """取知识库软删文档 id 集合 (内部端点, Issue 10 检索过滤用).

    软删立即生效 (Q18c): 检索时 (同步链路 search_kb/KbSource) 实时查询
    Django, 构造 Milvus filter 排除. 网络/4xx 错误抛 RuntimeError
    (检索接口转 503, 不静默返回脏数据).
    """
    path = f"/api/charplot/kb/{kb_id}/deleted-doc-ids/"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                f"{config.DJANGO_API_BASE}{path}", headers=_internal_headers()
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"取软删文档列表失败 (网络): {exc}") from exc
    if resp.status_code != 200:
        detail = resp.text[:200] if resp.text else resp.status_code
        raise RuntimeError(f"取软删文档列表失败 ({resp.status_code}): {detail}")
    return [int(doc_id) for doc_id in resp.json().get("deleted_doc_ids", [])]


class UserNotFoundError(RuntimeError):
    """内部端点用户不存在 (Issue 13): FastAPI 转 404, 与数据/服务错误区分."""


async def fetch_status_summary_input(user_id: int) -> dict:
    """取状态总结聚合输入 (内部端点, Issue 13, DESIGN.md §4.2).

    返回 {mastery, activity, weakpoints} (与 Dashboard 三个用户端点同构,
    按 user_id 查询实现用户隔离). 用户不存在 → UserNotFoundError (转 404);
    网络 / 其他 4xx / 5xx → RuntimeError (转 502, 前端可重试).
    """
    path = f"/api/charplot/users/{user_id}/status-summary-input/"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{config.DJANGO_API_BASE}{path}", headers=_internal_headers()
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"取状态总结聚合失败 (网络): {exc}") from exc
    if resp.status_code == 404:
        raise UserNotFoundError(f"用户 {user_id} 不存在")
    if resp.status_code != 200:
        detail = resp.text[:200] if resp.text else resp.status_code
        raise RuntimeError(f"取状态总结聚合失败 ({resp.status_code}): {detail}")
    return resp.json()
