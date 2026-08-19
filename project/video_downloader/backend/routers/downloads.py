"""POST /api/downloads + GET /api/tasks + GET /api/files/{id} (T02)."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..downloader import ResolveError
from ..schemas import DownloadRequest, DownloadResponse, TaskOut, task_to_out
from ..task_manager import STATUS_COMPLETED, manager

router = APIRouter(tags=["downloads"])


@router.post("/api/downloads", response_model=DownloadResponse)
def create_download(req: DownloadRequest) -> DownloadResponse:
    url = req.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=422, detail="链接必须以 http:// 或 https:// 开头"
        )
    try:
        task = manager.create_download(url, req.format_id)
    except (ResolveError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return DownloadResponse(task_id=task.id, status=task.status)


@router.get("/api/tasks")
def list_tasks() -> dict[str, list[TaskOut]]:
    return {"tasks": [task_to_out(t) for t in manager.list_tasks()]}


@router.get("/api/files/{task_id}")
def get_delivery_file(task_id: int) -> FileResponse:
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
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
