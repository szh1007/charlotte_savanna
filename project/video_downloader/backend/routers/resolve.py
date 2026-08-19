"""POST /api/resolve 解析视频链接元信息."""

from fastapi import APIRouter, HTTPException

from ..downloader import ResolveError
from ..schemas import FormatOut, ResolveRequest, ResolveResponse, ensure_http_url
from ..task_manager import manager

router = APIRouter(tags=["resolve"])


def _to_response(task) -> ResolveResponse:
    return ResolveResponse(
        task_id=task.id,
        status=task.status,
        title=task.title or "",
        cover=task.cover,
        duration=task.duration,
        site=task.site,
        formats=[FormatOut(**f) for f in task.formats],
    )


@router.post("/api/resolve", response_model=ResolveResponse)
def resolve_video(req: ResolveRequest) -> ResolveResponse:
    try:
        url = ensure_http_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        task = manager.resolve(url)
    except ResolveError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _to_response(task)
