"""流式 chunk 累积: delta 增量拼装为完整响应 (issue 01 检查项: delta 累积).

SSE 线协议逐行解析在 client 侧完成, 本模块只处理已反序列化的
chunk 对象 (SDK 端同样调用, 故不依赖 SSE 文本格式).

- content / reasoning_content 增量拼接 (#11)
- tool_calls 按 index 分片累积: id / name 首 chunk 给, arguments 增量拼接 (#10)
- 末块 usage 收尾累积 token 计量 (stream_options.include_usage)

增量字段类型校验与 parse.parse_chat_completion 一致: 畸形 chunk (如多模态
content 数组) 抛 ModelProtocolError 而非裸 TypeError.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from CharAgent.model.parse import parse_finish_reason, parse_usage
from CharAgent.model.utils.errors import ModelProtocolError
from CharAgent.model.utils.types import (
    FinishReason,
    ModelResponse,
    ModelToolCall,
    Usage,
)


@dataclass(slots=True)
class _ToolCallPart:
    """
    流式 tool_calls 按 index 的分片累积状态
    (#10: arguments 是分片 delta, 需拼接而非整段返回).
    """

    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass(slots=True)
class StreamAccumulator:
    """一次 SSE 流式响应的累积状态. chunk.choices[0].delta 的增量字段拼接为完整内容."""

    content: str = ""
    reasoning: str = ""
    tool_call_parts: dict[int, _ToolCallPart] = field(default_factory=dict)
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    model: str | None = None


def accumulate_delta(acc: StreamAccumulator, delta: Mapping[str, Any]) -> None:
    """
    累积单个 delta 分片:
        content / reasoning_content 增量拼接,
        tool_calls 按 index 分片累积.
    """
    content = delta.get("content")
    if content is not None:
        if not isinstance(content, str):
            raise ModelProtocolError(
                f"delta.content 应为字符串, 实际: {type(content).__name__}"
            )
        acc.content += content
    reasoning = delta.get("reasoning_content")
    if reasoning is not None:
        if not isinstance(reasoning, str):
            raise ModelProtocolError(
                f"delta.reasoning_content 应为字符串, 实际: {type(reasoning).__name__}"
            )
        acc.reasoning += reasoning
    for item in delta.get("tool_calls") or []:
        if not isinstance(item, dict):
            raise ModelProtocolError(
                f"tool_calls delta 元素应为对象, 实际: {type(item).__name__}"
            )
        index = item.get("index")
        if not isinstance(index, int):
            raise ModelProtocolError(f"tool_calls delta 缺整数 index: {item!r}")
        part = acc.tool_call_parts.setdefault(index, _ToolCallPart())
        call_id = item.get("id")
        if call_id is not None:
            if not isinstance(call_id, str):
                raise ModelProtocolError(
                    f"tool_calls delta id 应为字符串, 实际: {type(call_id).__name__}"
                )
            part.id = call_id
        function = item.get("function")
        if function is not None and not isinstance(function, dict):
            raise ModelProtocolError(
                f"tool_calls delta function 应为对象, 实际: {type(function).__name__}"
            )
        if isinstance(function, dict):
            name = function.get("name")
            if name is not None:
                if not isinstance(name, str):
                    raise ModelProtocolError(
                        f"tool_calls delta name 应为字符串, 实际: {type(name).__name__}"
                    )
                part.name = name
            arg_delta = function.get("arguments")
            if arg_delta is not None:
                if not isinstance(arg_delta, str):
                    raise ModelProtocolError(
                        "tool_calls delta arguments 应为字符串: "
                        f"{type(arg_delta).__name__}"
                    )
                part.arguments += arg_delta


def apply_sse_chunk(acc: StreamAccumulator, chunk: Mapping[str, Any]) -> None:
    """应用单个 SSE chunk.

    usage 的到达形态 (官方 stream_options.include_usage 约定): **不存在只含
    usage 的块** —— 统计信息附加在 [DONE] 之前的最后一个内容块上, 该块
    choices 只有一个元素, delta 无新增内容且 finish_reason 非 null. 开启
    include_usage 后所有块都带 usage 字段, 除末块外值均为 null.

    本函数先读 usage 再处理 choices, 两种时序都能正确累积 (choices 为空的
    块也容错跳过), 不依赖块的出现顺序.
    """
    if not isinstance(chunk, dict):
        raise ModelProtocolError(
            f"SSE chunk 应为 JSON 对象, 实际: {type(chunk).__name__}"
        )
    if chunk.get("model"):
        acc.model = chunk["model"]
    usage = chunk.get("usage")
    if usage:
        acc.usage = parse_usage(usage)
    choices = chunk.get("choices") or []
    if not choices:
        return  # 无 choices 的块 (容错分支, 官方不单独下发 usage 块)
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ModelProtocolError(
            f"chunk choices[0] 应为对象, 实际: {type(choice).__name__}"
        )
    delta = choice.get("delta")
    if isinstance(delta, dict) and delta:
        accumulate_delta(acc, delta)
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None:
        acc.finish_reason = parse_finish_reason(finish_reason)


def build_stream_response(acc: StreamAccumulator) -> ModelResponse:
    """流式累积状态 -> 完整 ModelResponse. 流结束仍无 finish_reason 视为断流畸形."""
    if acc.finish_reason is None:
        raise ModelProtocolError("SSE 流结束但未收到 finish_reason, 响应不完整")
    calls: list[ModelToolCall] = []
    for index in sorted(acc.tool_call_parts):
        part = acc.tool_call_parts[index]
        if not part.id or not part.name:
            raise ModelProtocolError(
                f"tool_calls 流式累积不完整 (index={index}): 缺 id 或 name"
            )
        calls.append(
            ModelToolCall(id=part.id, name=part.name, arguments=part.arguments)
        )
    return ModelResponse(
        content=acc.content or None,
        tool_calls=calls,
        finish_reason=acc.finish_reason,
        reasoning=acc.reasoning or None,
        usage=acc.usage,
        model=acc.model,
    )
