"""Pydantic 请求 / 响应模型 (API 契约, 见 DESIGN.md 第 4 节)."""

from urllib.parse import urlsplit

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


def ensure_bilibili_url(url: str) -> str:
    """校验并规范化哔哩哔哩链接 (http/https + 域名白名单), 非法时抛 ValueError.

    领域规则「仅支持哔哩哔哩免费公开视频」集中定义, resolve / downloads 路由共用
    (ADR-0004). 允许 bilibili.com 主域及任意子域 (www. / m. / player. 等) 与
    b23.tv 短链, 其余域名一律拒绝. 只约束用户输入, 引擎内部的短链跳转不受影响.
    """
    url = ensure_http_url(url)
    host = (urlsplit(url).hostname or "").lower()  # 不含端口, 已转小写
    if host == "b23.tv" or host == "bilibili.com" or host.endswith(".bilibili.com"):
        return url
    raise ValueError("仅支持哔哩哔哩 (bilibili.com / b23.tv) 链接")


class ResolveRequest(BaseModel):
    url: str = Field(..., min_length=1, description="视频页面链接")


class FormatOut(BaseModel):
    format_id: str
    label: str
    height: int | None
    ext: str
    locked: bool = False  # 该档位对该用户是否锁定 (免费用户 >720p, T05)
    has_audio: bool = True  # 是否含音频 (DASH 分离流 False: 下载时自动合并音频流)
    filesize: float | None = None  # 文件大小 (字节, 引擎缺失时为 None)


class SubtaskOut(BaseModel):
    """总结子任务状态 (四 tab 独立进度/错误, ADR-0005)."""

    status: str
    progress: float = 0.0
    error: str | None = None
    message: str | None = None


class ResolveResponse(BaseModel):
    task_id: int
    status: str
    title: str
    cover: str | None = None
    duration: float | None = None
    site: str | None = None
    uploader: str | None = None  # up主
    view_count: int | None = None  # 播放量
    description: str | None = None  # 简介
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
    url: str = ""  # 源视频链接 (前端按视频分组展示, 同视频多清晰度合并一行)
    title: str = ""
    cover: str | None = None
    duration: float | None = None
    site: str | None = None
    uploader: str | None = None  # up主
    view_count: int | None = None  # 播放量
    description: str | None = None  # 简介
    formats: list[FormatOut] = Field(default_factory=list)
    format_id: str | None = None  # 选定档位 (标题旁清晰度标注用)
    progress: float = 0.0
    # 四标签独立进度 (总结任务, 下载任务恒 0): 转录 0-100 / 总结生成中 30, 完成 100
    transcript_progress: float = 0.0
    summary_progress: float = 0.0
    # 四子任务状态 (kind=summary; 下载任务为空 dict), 前端逐 tab 驱动
    subtasks: dict[str, SubtaskOut] = Field(default_factory=dict)
    message: str | None = None
    error: str | None = None
    expires_at: float | None = None  # 交付过期时刻 (仅 completed, 前端倒计时)
    created_at: float


class SummarizeRequest(BaseModel):
    url: str = Field(..., min_length=1, description="视频页面链接")


class RetryRequest(BaseModel):
    """重试总结子任务 (ADR-0005): 仅失败/阻塞的子任务可重试."""

    subtask: str = Field(
        ..., pattern="^(transcript|summary|mindmap|qa)$", description="子任务名"
    )


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class SummaryOut(BaseModel):
    """总结结果 (契约 ADR-0005): 结构化总结 JSON + 元信息."""

    task_id: int
    status: str
    title: str = ""
    cover: str | None = None
    duration: float | None = None
    summary: dict | None = None
    created_at: float


class TranscriptOut(BaseModel):
    """转录文本: 时间戳段列表 + 组合文本 (复制/导出用)."""

    task_id: int
    status: str
    segments: list[TranscriptSegment]
    text: str


class MindMapOut(BaseModel):
    """思维导图结果 (ADR-0005): 基于结构化总结生成的导图结构 JSON."""

    task_id: int
    status: str
    title: str = ""
    duration: float | None = None
    mindmap: dict | None = None
    created_at: float


class QARequest(BaseModel):
    question: str = Field(..., min_length=1, description="针对视频内容的问题")


def task_to_out(task) -> TaskOut:
    """Task (dataclass) → TaskOut, 供任务列表 / 单任务共用.

    SSE 事件负载见 events.task_event (字段子集 + 派生的直链 url, 与任务列表契约不同).
    """
    return TaskOut(
        task_id=task.id,
        kind=task.kind,
        status=task.status,
        url=task.url,
        title=task.title or "",
        cover=task.cover,
        duration=task.duration,
        site=task.site,
        uploader=task.uploader,
        view_count=task.view_count,
        description=task.description,
        formats=[FormatOut(**f) for f in task.formats],
        format_id=task.format_id,
        progress=task.progress,
        transcript_progress=task.transcript_progress,
        summary_progress=task.summary_progress,
        subtasks={
            name: SubtaskOut(
                status=sub.status,
                progress=sub.progress,
                error=sub.error,
                message=sub.message,
            )
            for name, sub in task.subtasks.items()
        },
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
