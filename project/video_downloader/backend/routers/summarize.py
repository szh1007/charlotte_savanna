"""AI 总结路由 (ADR-0005): 创建总结任务 / 结果查询 / 转录 / 问答 / 导出.

契约:
- POST /api/summarize {url} → {task_id, status}; 同 url 活跃任务幂等返回;
  免费超每日配额 429
- GET  /api/tasks/{id}/summary → 结构化总结 (章节时间线 + 要点)
- GET  /api/tasks/{id}/transcript → 带时间戳转录文本 (转录子任务完成即可访问)
- GET  /api/tasks/{id}/mindmap → 思维导图结构 (独立 LLM 生成)
- POST /api/tasks/{id}/qa {question} → {answer}; 免费超每日配额 429
- POST /api/tasks/{id}/retry {subtask} → 重试失败/阻塞的子任务 (不扣配额)
- GET  /api/tasks/{id}/export?format=md|txt|srt|vtt → 总结/转录导出

四子任务独立运行: transcript → summary/mindmap (依赖转录) → qa (依赖
转录+总结, 就绪后交互问答). 各接口按子任务完成态判定可用性 (转录先完成
即可先查看), 不再等整个任务 completed (ADR-0005).

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
    MindMapOut,
    QARequest,
    QAResponse,
    RetryRequest,
    SummarizeRequest,
    SummaryOut,
    TranscriptOut,
    TranscriptSegment,
    ensure_bilibili_url,
)
from ..task_manager import (
    ST_DONE,
    STATUS_EXPIRED,
    SUBTASK_MINDMAP,
    SUBTASK_QA,
    SUBTASK_SUMMARY,
    SUBTASK_TRANSCRIPT,
    manager,
    segments_to_text,
)

router = APIRouter(tags=["summarize"])


def _require_summary_task(task_id: int):
    """定位总结任务并校验可访问状态: 404 不存在 / 400 非总结任务.

    完成态由调用方按需判定 (各子任务 done; 未完成 409, 过期 410).
    """
    task = manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.kind != "summary":
        raise HTTPException(status_code=400, detail="任务不是总结任务")
    return task


def _require_subtask_done(task, name: str) -> None:
    """子任务完成态校验: 子任务未 done → 409, 过期 → 410.

    四子任务独立运行: 转录先完成即可先查看, 无需等整个任务完成.
    """
    if task.status == STATUS_EXPIRED:
        raise HTTPException(status_code=410, detail="总结结果已过期清理, 请重新总结")
    sub = task.subtasks.get(name)
    if sub is None or sub.status != ST_DONE:
        raise HTTPException(status_code=409, detail=f"{name} 尚未完成")


@router.post("/api/summarize")
def create_summarize(
    req: SummarizeRequest,
    member: MemberSession | None = Depends(get_member),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> dict[str, Any]:
    """创建总结任务 (免费档检查每日配额, 会员不限).

    幂等: 同 url 已有活跃 (queued/running) 总结任务时直接返回已有任务,
    不重复创建、不重复扣配额 (按钮防重复点击, 前端刷新后仍收敛).
    """
    try:
        url = ensure_bilibili_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    existing = manager.find_active_summary(url)
    if existing is not None:
        return {"task_id": existing.id, "status": existing.status}
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
    """结构化总结结果 (章节时间线 + 要点, 前端 md 渲染)."""
    task = _require_summary_task(task_id)
    _require_subtask_done(task, SUBTASK_SUMMARY)
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
    """带时间戳转录全文 (查看 / 复制 / 导出原料).

    转录子任务完成即可访问 (总结/导图仍在生成时也能先查看字幕).
    """
    task = _require_summary_task(task_id)
    _require_subtask_done(task, SUBTASK_TRANSCRIPT)
    segments = task.transcript or []
    return TranscriptOut(
        task_id=task.id,
        status=task.status,
        segments=[TranscriptSegment(**s) for s in segments],
        text=segments_to_text(segments),
    )


@router.get("/api/tasks/{task_id}/mindmap", response_model=MindMapOut)
def get_mindmap(task_id: int) -> MindMapOut:
    """思维导图结构 (独立 LLM 生成, 与总结摘要解耦, 独立完成/失败/重试)."""
    task = _require_summary_task(task_id)
    _require_subtask_done(task, SUBTASK_MINDMAP)
    return MindMapOut(
        task_id=task.id,
        status=task.status,
        title=task.title or "",
        duration=task.duration,
        mindmap=task.mindmap,
        created_at=task.created_at,
    )


@router.post("/api/tasks/{task_id}/retry")
def retry_task(
    task_id: int,
    req: RetryRequest,
) -> dict[str, Any]:
    """重试失败的总结子任务 (转录/总结/导图/问答), 不扣配额.

    只重跑失败 (failed/blocked) 的子任务, done 子任务保留结果; 依赖恢复
    的 blocked 子任务由调度器 DAG 扫描自动解锁. 重试不检查免费档每日配额
    (修复性操作, 非新消费).
    """
    task = _require_summary_task(task_id)
    try:
        manager.retry_subtask(task_id, req.subtask)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"task_id": task.id, "status": task.status}


@router.post("/api/tasks/{task_id}/qa", response_model=QAResponse)
def qa(
    task_id: int,
    req: QARequest,
    member: MemberSession | None = Depends(get_member),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> QAResponse:
    """针对视频内容提问 (上下文 = 转录 + 结构化总结, 免费档配额检查).

    问答子任务 (qa) done 即上下文就绪, 解锁交互问答; 不需要等任务 completed.
    """
    task = _require_summary_task(task_id)
    _require_subtask_done(task, SUBTASK_QA)
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
    export_format: str = Query(
        default="md", alias="format", pattern="^(md|txt|srt|vtt)$"
    ),
) -> Response:
    """导出总结 (md) 或转录 (txt/srt/vtt), 供用户本地永久保存 (与 TTL 无关).

    可用性按子任务判定: md 需总结完成; txt/srt/vtt 需转录完成.
    query 键为 format (契约), alias 避免遮蔽内置 format.
    """
    task = _require_summary_task(task_id)
    if export_format == "md":
        _require_subtask_done(task, SUBTASK_SUMMARY)
        content = _render_markdown(task)
        media_type = "text/markdown; charset=utf-8"
        filename = f"summary_{task.id}.md"
    elif export_format == "txt":
        _require_subtask_done(task, SUBTASK_TRANSCRIPT)
        content = segments_to_text(task.transcript or [])
        media_type = "text/plain; charset=utf-8"
        filename = f"transcript_{task.id}.txt"
    elif export_format == "srt":
        _require_subtask_done(task, SUBTASK_TRANSCRIPT)
        content = _render_srt(task.transcript or [])
        media_type = "text/plain; charset=utf-8"
        filename = f"transcript_{task.id}.srt"
    else:  # vtt
        _require_subtask_done(task, SUBTASK_TRANSCRIPT)
        content = _render_vtt(task.transcript or [])
        media_type = "text/vtt; charset=utf-8"
        filename = f"transcript_{task.id}.vtt"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _fmt_timestamp(seconds: float, sep: str = ",", vtt: bool = False) -> str:
    """秒 → 字幕时间戳: srt 用逗号毫秒, vtt 用点号毫秒 (HH:MM:SS,mmm)."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = round((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _render_srt(segments: list[dict[str, Any]]) -> str:
    """转录段 → SRT 字幕 (序号 + 时间轴 + 文本, 空行分隔)."""
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = _fmt_timestamp(seg["start"])
        end = _fmt_timestamp(seg["end"])
        lines.extend([str(i), f"{start} --> {end}", seg["text"], ""])
    return "\n".join(lines)


def _render_vtt(segments: list[dict[str, Any]]) -> str:
    """转录段 → WebVTT 字幕 (WEBVTT 头 + 点号毫秒时间轴)."""
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _fmt_timestamp(seg["start"], sep=".", vtt=True)
        end = _fmt_timestamp(seg["end"], sep=".", vtt=True)
        lines.extend([f"{start} --> {end}", seg["text"], ""])
    return "\n".join(lines)


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
