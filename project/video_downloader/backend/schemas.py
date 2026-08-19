"""Pydantic 请求 / 响应模型 (API 契约, 见 DESIGN.md 第 4 节)."""

from pydantic import BaseModel, Field

from . import config
from .task_manager import STATUS_COMPLETED


def ensure_http_url(url: str) -> str:
    """校验并规范化视频链接 (仅 http/https), 非法时抛 ValueError.

    领域规则「合法链接」集中定义, resolve / downloads 路由共用.
    """
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("链接必须以 http:// 或 https:// 开头")
    return url


class ResolveRequest(BaseModel):
    url: str = Field(..., min_length=1, description="视频页面链接")


class FormatOut(BaseModel):
    format_id: str
    label: str
    height: int | None
    ext: str
    locked: bool = False  # 该档位对该用户是否锁定 (免费用户 >720p, T05)
    has_audio: bool = True  # 是否含音频 (DASH 分离流 False: 下载时自动合并音频流)


class ResolveResponse(BaseModel):
    task_id: int
    status: str
    title: str
    cover: str | None = None
    duration: float | None = None
    site: str | None = None
    formats: list[FormatOut]
    member_limited: bool = False  # 是否存在会员专属 (锁定) 档位


class DownloadRequest(BaseModel):
    url: str = Field(..., min_length=1, description="视频页面链接")
    format_id: str = Field(..., min_length=1, description="选定档位 format_id")


class DownloadResponse(BaseModel):
    task_id: int
    status: str


class MemberRequest(BaseModel):
    key: str = Field(..., min_length=1, description="会员密钥")


class MemberResponse(BaseModel):
    is_member: bool
    expires_at: float
    token: str


class MemberStatusResponse(BaseModel):
    is_member: bool
    expires_at: float | None = None


class TaskOut(BaseModel):
    task_id: int
    kind: str
    status: str
    title: str = ""
    cover: str | None = None
    duration: float | None = None
    site: str | None = None
    formats: list[FormatOut] = Field(default_factory=list)
    format_id: str | None = None  # 选定档位 (标题旁清晰度标注用)
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    expires_at: float | None = None  # 交付过期时刻 (仅 completed, 前端倒计时)
    created_at: float


def task_to_out(task) -> TaskOut:
    """Task (dataclass) → TaskOut, 供任务列表 / 单任务共用.

    SSE 事件负载见 events.task_event (字段子集 + 派生的直链 url, 与任务列表契约不同).
    """
    return TaskOut(
        task_id=task.id,
        kind=task.kind,
        status=task.status,
        title=task.title or "",
        cover=task.cover,
        duration=task.duration,
        site=task.site,
        formats=[FormatOut(**f) for f in task.formats],
        format_id=task.format_id,
        progress=task.progress,
        message=task.message,
        error=task.error,
        # 交付过期时刻 = 完成时刻 + 身份 TTL; 仅 completed 有交付资产, 其余为 None
        expires_at=(
            task.completed_at + config.delivery_ttl(task.is_member)
            if task.status == STATUS_COMPLETED and task.completed_at
            else None
        ),
        created_at=task.created_at,
    )
