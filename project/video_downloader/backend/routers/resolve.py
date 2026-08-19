"""POST /api/resolve 解析视频链接元信息 (T05: 按会员身份标记档位锁定)."""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import MemberSession, get_member
from ..downloader import ResolveError
from ..schemas import FormatOut, ResolveRequest, ResolveResponse, ensure_bilibili_url
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
        member_limited=any(f["locked"] for f in task.formats),
    )


@router.post("/api/resolve", response_model=ResolveResponse)
def resolve_video(
    req: ResolveRequest,
    member: MemberSession | None = Depends(get_member),
) -> ResolveResponse:
    try:
        url = ensure_bilibili_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        task = manager.resolve(url, is_member=member is not None)
    except ResolveError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _to_response(task)
