"""下载/任务/文件路由 + 清除记录 (T02, T05, T10)."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import MemberSession, get_member
from ..cleaner import cleaner
from ..downloader import ResolveError
from ..schemas import (
    DownloadRequest,
    DownloadResponse,
    TaskOut,
    ensure_http_url,
    task_to_out,
)
from ..task_manager import STATUS_COMPLETED, STATUS_EXPIRED, QueueLimitError, manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["downloads"])


@router.post("/api/downloads", response_model=DownloadResponse)
def create_download(
    req: DownloadRequest,
    member: MemberSession | None = Depends(get_member),
) -> DownloadResponse:
    try:
        url = ensure_http_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        task = manager.create_download(url, req.format_id, is_member=member is not None)
    except QueueLimitError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except (ResolveError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return DownloadResponse(task_id=task.id, status=task.status)


@router.get("/api/tasks")
def list_tasks() -> dict[str, list[TaskOut]]:
    return {"tasks": [task_to_out(t) for t in manager.list_tasks()]}


@router.get("/api/tasks/{task_id}")
def get_task_detail(task_id: int) -> TaskOut:
    """单任务详情 (契约 PRD §8): 与列表同一序列化结构, 不存在返回 404."""
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task_to_out(task)


@router.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int) -> None:
    """清除单条任务记录: 删除交付文件 + 移除任务, 广播 removed 事件.

    任意状态均可清除 (用户反馈: 非已完成任务也要能删, 特别是失败记录):
    进行中任务 (排队/下载中) 经 remove_task 置取消信号中断引擎, yt-dlp
    清理临时文件, 任务 file_path 未回填无残留. 文件删除失败 (占用等)
    返回 409 提示重试, 任务保留不丢失.
    """
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.file_path:
        try:
            Path(task.file_path).unlink(missing_ok=True)  # 锁外 IO, 幂等
        except OSError as e:
            logger.warning(
                "清除记录删除文件失败 task=%s path=%s: %s",
                task_id,
                task.file_path,
                e,
            )
            raise HTTPException(
                status_code=409, detail="视频文件删除失败 (可能被其他程序占用), 请重试"
            ) from e
    manager.remove_task(task_id)


@router.post("/api/tasks/purge-unfinished")
def purge_unfinished_tasks() -> dict[str, list[int]]:
    """一键清除全部未完成记录: 立即生效 (不等 60s 周期清理).

    清除范围: 未完成任务 (expired / failed / 排队中 / 下载中取消) + 已超
    TTL 的 completed, 顺带清理 DOWNLOADS_DIR 中的孤儿文件 (无任务引用且
    超 24h, 见 cleaner.purge_unfinished).
    """
    return {"removed": cleaner.purge_unfinished()}


@router.get("/api/files/{task_id}")
def get_delivery_file(task_id: int) -> FileResponse:
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == STATUS_EXPIRED:
        # 曾存在但已移除: 410 Gone 语义 + 明确提示 (验收标准, T06)
        raise HTTPException(status_code=410, detail="交付链接已过期, 文件已清理")
    if task.status != STATUS_COMPLETED or not task.file_path:
        raise HTTPException(status_code=404, detail="文件未就绪或任务未完成")
    path = Path(task.file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已清理")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="attachment",
    )
