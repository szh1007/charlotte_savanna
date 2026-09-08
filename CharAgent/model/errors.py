"""模型层异常语义: 瞬态 / 永久区分, 供 retry (P0-5) 判断.

- 瞬态 (retryable=True): 429 / 5xx / 连接失败 / 超时
- 永久 (retryable=False): 其余 4xx / 配置错误 / 响应畸形
"""

from __future__ import annotations


class ModelError(Exception):
    """模型调用失败基类. retryable=True 表示瞬态错误 (可重试), False 表示永久错误."""

    retryable: bool = False


class ModelConfigError(ModelError):
    """配置错误 (缺 API Key 等), 永久错误."""


class ModelConnectionError(ModelError):
    """网络不可达 / 连接中断, 瞬态错误."""

    retryable = True


class ModelTimeoutError(ModelConnectionError):
    """请求超时 (适配器 timeout 由构造参数配置, P1-3 分层超时的 model 层)."""


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
    ) -> None:
        if retryable is None:
            retryable = status_code == 429 or 500 <= status_code < 600
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"模型 API 返回 HTTP {status_code}: {message}")


class ModelProtocolError(ModelError):
    """
    响应畸形 (字段缺失 / 非法 JSON / 未知 finish_reason),
    同输入大概率同输出, 视为永久错误.
    """
