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
    """模型响应的终止原因, 决定 loop 继续还是结束 (difficulties #10)."""

    STOP = "stop"  # 正常结束, 可返回给用户
    TOOL_CALLS = "tool_calls"  # 模型要调工具, loop 继续
    LENGTH = "length"  # token 截断, 结果不完整, 需续写或精简 (#10)
    CONTENT_FILTER = "content_filter"  # 被安全策略拦截


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
    (OpenAI usage 结构映射; reasoning_tokens 为 DeepSeek 扩展, 缺失时 None).
    """

    input_tokens: int | None = None  # prompt_tokens
    output_tokens: int | None = None  # completion_tokens
    total_tokens: int | None = None
    reasoning_tokens: int | None = None  # 推理 token 数, 计入成本与上下文 (#11)


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
