"""模型层异常语义: 瞬态 / 永久区分, 供 retry 层判断.

- 瞬态 (retryable=True): 429 / 5xx / 连接失败 / 超时
- 永久 (retryable=False): 其余 4xx / 配置错误 / 响应畸形
"""

from __future__ import annotations

from enum import StrEnum


class ModelErrorKind(StrEnum):
    """上游错误的**语义**分类 (短标识, 机器读的).

    为什么光有 status_code 不够: 「输入超窗口」与「参数写错」都是 400, 而框架对
    它们要做的事完全不同 —— 前者**压一次再发**就成了 (2026-09-23 的超限兜底),
    后者重发多少次都一样. 判据只写在解析层一处 (`parse.classify_error`), 上层只认
    这个 kind, 谁也不再自己去抠错误正文的字符串.
    """

    CONTEXT_OVERFLOW = "context_overflow"  # 输入超出窗口 (压一次可救)
    RATE_LIMITED = "rate_limited"  # 429
    SERVER = "server"  # 5xx
    INVALID_REQUEST = "invalid_request"  # 其余 4xx (重发无用)
    UNKNOWN = "unknown"


class ModelError(Exception):
    """模型调用失败基类. retryable=True 表示瞬态错误 (可重试), False 表示永久错误."""

    retryable: bool = False


class ModelConfigError(ModelError):
    """配置错误 (缺 API Key 等), 永久错误."""


class ModelConnectionError(ModelError):
    """网络不可达 / 连接中断, 瞬态错误."""

    retryable = True


class ModelTimeoutError(ModelConnectionError):
    """请求超时 (适配器 timeout 由构造参数配置, 分层超时的 model 层)."""


class ModelStatusError(ModelError):
    """
    非 2xx HTTP 状态码.
    429 / 5xx 为瞬态 (retryable=True),
    其余 4xx 为永久 (retryable=False).
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        retryable: bool | None = None,
        kind: ModelErrorKind | None = None,
    ) -> None:
        if retryable is None:
            retryable = status_code == 429 or 500 <= status_code < 600
        self.status_code = status_code
        self.retryable = retryable
        # 适配器按完整错误体判 (见 parse.classify_error); 没判就记 unknown —— 宁可
        # 说「不知道」, 也不要凭状态码猜一个可能错的语义
        self.kind = ModelErrorKind.UNKNOWN if kind is None else kind
        super().__init__(f"模型 API 返回 HTTP {status_code}: {message}")

    @property
    def is_context_overflow(self) -> bool:
        """上游是不是在说「这份输入超了窗口」—— loop 靠它决定要不要紧急压一次."""
        return self.kind is ModelErrorKind.CONTEXT_OVERFLOW


class ModelProtocolError(ModelError):
    """
    响应畸形 (字段缺失 / 非法 JSON / 未知 finish_reason),
    同输入大概率同输出, 视为永久错误.
    """
