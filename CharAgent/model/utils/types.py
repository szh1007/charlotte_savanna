"""模型层响应数据结构: wire 类型别名与协议数据类.

FinishReason 决定 agent loop 继续还是结束; reasoning 与正文分离存储 (#11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# OpenAI 兼容 wire 消息: {"role", "content"}, assistant 可带 tool_calls,
# tool 回填消息带 tool_call_id
type ModelMessage = dict[str, Any]
# tools 参数: JSON Schema 描述的工具列表 (P0-2 的 @tool 装饰器产出), 直通协议
type ToolSpec = dict[str, Any]


class FinishReason(StrEnum):
    """模型响应的终止原因, 决定 loop 继续还是结束 (difficulties #10).

    六个取值与官方 /chat/completions 枚举一一对应; 后两个是**服务端中断**
    (非模型自然结束), 内容可能只是半截 —— AgentLoop 据此判 SERVER_INTERRUPTED,
    不当最终答复返回.
    """

    STOP = "stop"  # 正常结束, 可返回给用户
    TOOL_CALLS = "tool_calls"  # 模型要调工具, loop 继续
    LENGTH = "length"  # token 截断, 结果不完整, 需续写或精简 (#10)
    CONTENT_FILTER = "content_filter"  # 被安全策略拦截
    INSUFFICIENT_SYSTEM_RESOURCE = (
        "insufficient_system_resource"  # 服务端推理资源不足, 生成被打断 (瞬态)
    )
    ABORTED = "aborted"  # 生成过程被中断


@dataclass(slots=True)
class ModelToolCall:
    """
    模型发起的一次工具调用
    (difficulties #10: tool_calls 是数组, 并行语义在 loop层保持).
    """

    id: str  # tool_call_id, tool 回填消息需原样携带
    name: str  # 工具名
    arguments: str  # 参数 JSON 字符串, 保真不预解析 (#2: 畸形 JSON 交给工具执行层纠错)


@dataclass(slots=True)
class Usage:
    """
    token 计量
    (OpenAI usage 结构映射; reasoning / 缓存字段为 DeepSeek 扩展, 缺失时 None).

    字段层级对齐官方响应 schema:
    - reasoning_tokens 位于 usage.completion_tokens_details 下 (非顶层)
    - cache_hit_tokens 取顶层 prompt_cache_hit_tokens, 与
      prompt_tokens_details.cached_tokens 同值 (后者作兼容回退)
    """

    input_tokens: int | None = None  # prompt_tokens
    output_tokens: int | None = None  # completion_tokens
    total_tokens: int | None = None
    # completion_tokens_details.reasoning_tokens, 计入成本与上下文 (#11)
    reasoning_tokens: int | None = None
    # prompt_cache_hit_tokens: 命中上下文缓存的输入 token
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None  # prompt_cache_miss_tokens: 未命中缓存的输入


@dataclass(slots=True)
class ModelResponse:
    """模型的一次响应: 文本 content 或 tool_calls 列表 (可两者并存)."""

    content: str | None  # 最终文本; 纯工具调用回复常为 None
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    finish_reason: FinishReason = FinishReason.STOP
    reasoning: str | None = None  # reasoning_content 与 content 分离 (#11)
    usage: Usage | None = None
    model: str | None = None  # API 回显的模型名
    raw: dict[str, Any] | None = None  # 原始响应, 供排查与录制回放样本 (#61)

    @property
    def has_tool_calls(self) -> bool:
        """loop 判断是否进入工具执行分支."""
        return bool(self.tool_calls)
