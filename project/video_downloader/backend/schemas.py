"""Pydantic 请求 / 响应模型 (API 契约, 见 DESIGN.md 第 4 节)."""

from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    url: str = Field(..., min_length=1, description="视频页面链接")


class FormatOut(BaseModel):
    format_id: str
    label: str
    height: int | None
    ext: str


class ResolveResponse(BaseModel):
    task_id: int
    status: str
    title: str
    cover: str | None = None
    duration: float | None = None
    site: str | None = None
    formats: list[FormatOut]
