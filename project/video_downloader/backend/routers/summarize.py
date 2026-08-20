"""AI 总结路由 (ADR-0005): 创建总结任务 / 结果查询 / 转录 / 问答 / 导出.

契约:
- POST /api/summarize {url} → {task_id, status}; 免费超每日配额 429
- GET  /api/tasks/{id}/summary → 结构化总结 (章节时间线 + 要点)
- GET  /api/tasks/{id}/transcript → 带时间戳转录文本
- POST /api/tasks/{id}/qa {question} → {answer}; 免费超每日配额 429
- GET  /api/tasks/{id}/export?format=md|txt → 总结 Markdown / 转录 TXT

免费配额按匿名 client_id (X-Client-Id header, 前端 localStorage 持久化)
计数, 会员 (有效 X-Member-Token) 不限 (ADR-0005).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response

from .. import llm
from ..auth import MemberSession, get_member
from ..quota import QA, SUMMARY, QuotaExceededError, quota
from ..schemas import (
    QARequest,
    QAResponse,
    SummarizeRequest,
    SummaryOut,
    TranscriptOut,
    TranscriptSegment,
    ensure_bilibili_url,
)
from ..task_manager import STATUS_COMPLETED, STATUS_EXPIRED, manager, segments_to_text

router = APIRouter(tags=["summarize"])


def _require_summary_task(task_id: int):
    """定位总结任务并校验可访问状态: 404 不存在 / 400 非总结任务.

    完成态由调用方按需判定 (summary/transcript 需 completed, 导出同;
    未完成 409, 过期 410).
    """
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.kind != "summary":
        raise HTTPException(status_code=400, detail="任务不是总结任务")
    return task


def _require_completed(task) -> None:
    """完成态校验: 未完成 409 (前端可按进度提示), 过期 410."""
    if task.status == STATUS_EXPIRED:
        raise HTTPException(status_code=410, detail="总结结果已过期清理, 请重新总结")
    if task.status != STATUS_COMPLETED:
        raise HTTPException(status_code=409, detail="总结尚未完成")


@router.post("/api/summarize")
def create_summarize(
    req: SummarizeRequest,
    member: MemberSession | None = Depends(get_member),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> dict[str, Any]:
    """创建总结任务 (免费档检查每日配额, 会员不限)."""
    try:
        url = ensure_bilibili_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if member is None:  # 免费档配额检查 (会员跳过)
        try:
            quota.check(x_client_id or "", SUMMARY)
        except QuotaExceededError as e:
            raise HTTPException(status_code=429, detail=str(e)) from e
    task = manager.create_summary(url, is_member=member is not None)
    if member is None:
        quota.use(x_client_id or "", SUMMARY)  # 任务创建成功才计数
    return {"task_id": task.id, "status": task.status}


@router.get("/api/tasks/{task_id}/summary", response_model=SummaryOut)
def get_summary(task_id: int) -> SummaryOut:
    """结构化总结结果 (章节时间线 + 要点, 前端渲染与思维导图数据源)."""
    task = _require_summary_task(task_id)
    _require_completed(task)
    return SummaryOut(
        task_id=task.id,
        status=task.status,
        title=task.title or "",
        cover=task.cover,
        duration=task.duration,
        summary=task.summary,
        created_at=task.created_at,
    )


@router.get("/api/tasks/{task_id}/transcript", response_model=TranscriptOut)
def get_transcript(task_id: int) -> TranscriptOut:
    """带时间戳转录全文 (查看 / 复制 / 导出原料)."""
    task = _require_summary_task(task_id)
    _require_completed(task)
    segments = task.transcript or []
    return TranscriptOut(
        task_id=task.id,
        status=task.status,
        segments=[TranscriptSegment(**s) for s in segments],
        text=segments_to_text(segments),
    )


@router.post("/api/tasks/{task_id}/qa", response_model=QAResponse)
def qa(
    task_id: int,
    req: QARequest,
    member: MemberSession | None = Depends(get_member),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> QAResponse:
    """针对视频内容提问 (上下文 = 转录 + 结构化总结, 免费档配额检查)."""
    task = _require_summary_task(task_id)
    _require_completed(task)
    if member is None:
        try:
            quota.check(x_client_id or "", QA)
        except QuotaExceededError as e:
            raise HTTPException(status_code=429, detail=str(e)) from e
    try:
        answer = llm.ask(
            segments_to_text(task.transcript or []),
            task.summary or {},
            req.question,
        )
    except llm.LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if member is None:
        quota.use(x_client_id or "", QA)
    return QAResponse(task_id=task.id, answer=answer)


@router.get("/api/tasks/{task_id}/export")
def export_summary(
    task_id: int,
    export_format: str = Query(default="md", alias="format", pattern="^(md|txt)$"),
) -> Response:
    """导出总结 (md) 或转录全文 (txt), 供用户本地永久保存 (与 TTL 无关).

    query 键为 format (契约), alias 避免遮蔽内置 format.
    """
    task = _require_summary_task(task_id)
    _require_completed(task)
    if export_format == "txt":
        content = segments_to_text(task.transcript or [])
        media_type = "text/plain; charset=utf-8"
        filename = f"transcript_{task.id}.txt"
    else:
        content = _render_markdown(task)
        media_type = "text/markdown; charset=utf-8"
        filename = f"summary_{task.id}.md"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render_markdown(task) -> str:
    """结构化总结 → Markdown (导出: 概述 / 章节时间线 / 要点 / 结论)."""
    summary = task.summary or {}
    out = [f"# 视频总结: {task.title or '未知标题'}"]
    if task.duration:
        out.append(f"> 时长: {int(task.duration)}s")
    out.extend(
        [
            "",
            "## 概述",
            str(summary.get("overview") or ""),
            "",
            "## 章节时间线",
        ]
    )
    for ch in summary.get("chapters") or []:
        mm, ss = divmod(int(ch.get("start", 0)), 60)
        end_mm, end_ss = divmod(int(ch.get("end", 0)), 60)
        span = f"{mm:02d}:{ss:02d} ~ {end_mm:02d}:{end_ss:02d}"
        out.append(f"### {ch.get('title')} ({span})")
        for point in ch.get("points") or []:
            out.append(f"- {point}")
    out.extend(["", "## 核心知识点"])
    for key in summary.get("key_points") or []:
        out.append(f"- {key}")
    out.extend(["", "## 结论", str(summary.get("conclusion") or "")])
    return "\n".join(out) + "\n"
