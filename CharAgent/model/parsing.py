"""非流式 /chat/completions 响应解析 (纯函数, 不依赖网络, 可直接单测).

字段缺失视为畸形响应并显式抛 ModelProtocolError, 供上层决定重试或纠错
(而非静默返回残缺结果).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from CharAgent.model.errors import ModelProtocolError
from CharAgent.model.types import (
    FinishReason,
    ModelResponse,
    ModelToolCall,
    Usage,
)


def parse_finish_reason(value: Any) -> FinishReason:
    """finish_reason 四值映射; 未知取值视为协议畸形, 显式报错而非静默吞掉."""
    if isinstance(value, FinishReason):
        return value
    try:
        return FinishReason(str(value))
    except ValueError as exc:
        raise ModelProtocolError(f"未知 finish_reason: {value!r}") from exc


def parse_usage(data: Mapping[str, Any] | None) -> Usage | None:
    """usage 字段映射; 响应不带 usage 时返回 None (流式未开 include_usage 的场景)."""
    if not data:
        return None
    return Usage(
        input_tokens=data.get("prompt_tokens"),
        output_tokens=data.get("completion_tokens"),
        total_tokens=data.get("total_tokens"),
        reasoning_tokens=data.get("reasoning_tokens"),
    )


def parse_tool_calls(items: Any) -> list[ModelToolCall]:
    """message.tool_calls 结构解析: [{"id", "function": {"name", "arguments"}}] -> 列表.

    arguments 保留原始 JSON 字符串;
    结构缺 id / name 视为畸形 (id 是 tool 回填配对键 #10).
    """
    if items is None:
        return []
    if not isinstance(items, list):
        raise ModelProtocolError(f"tool_calls 应为列表, 实际: {type(items).__name__}")
    calls: list[ModelToolCall] = []
    for item in items:
        if not isinstance(item, dict):
            raise ModelProtocolError(
                f"tool_calls 元素应为对象, 实际: {type(item).__name__}"
            )
        function = item.get("function")
        if not isinstance(function, dict):
            raise ModelProtocolError(f"tool_call 缺 function 结构: {item!r}")
        call_id = item.get("id")
        name = function.get("name")
        if not call_id or not isinstance(call_id, str):
            raise ModelProtocolError(f"tool_call 缺 id: {item!r}")
        if not name or not isinstance(name, str):
            raise ModelProtocolError(f"tool_call 缺 function.name: {item!r}")
        arguments = function.get("arguments")
        if arguments is None:
            arguments = ""
        if not isinstance(arguments, str):
            raise ModelProtocolError(
                f"tool_call arguments 应为 JSON 字符串: {type(arguments).__name__}"
            )
        calls.append(ModelToolCall(id=call_id, name=name, arguments=arguments))
    return calls


def parse_chat_completion(data: Mapping[str, Any]) -> ModelResponse:
    """非流式 /chat/completions 响应 JSON -> ModelResponse.

    字段缺失视为畸形响应并显式报错, 供上层决定重试或纠错 (而非静默返回残缺结果).
    """
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelProtocolError("响应缺少非空 choices 列表")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ModelProtocolError(f"choices[0] 应为对象, 实际: {type(choice).__name__}")
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise ModelProtocolError(f"message 应为对象, 实际: {type(message).__name__}")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        # OpenAI 新版多模态 content 为数组, 本项目明确排除多模态 (#52-54 属 P2)
        raise ModelProtocolError(
            f"content 应为字符串或 null, 实际: {type(content).__name__}"
        )
    reasoning = message.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        raise ModelProtocolError(
            f"reasoning_content 应为字符串或 null, 实际: {type(reasoning).__name__}"
        )
    return ModelResponse(
        content=content,
        tool_calls=parse_tool_calls(message.get("tool_calls")),
        finish_reason=parse_finish_reason(choice.get("finish_reason")),
        reasoning=reasoning,
        usage=parse_usage(data.get("usage")),
        model=data.get("model"),
        raw=dict(data),
    )
