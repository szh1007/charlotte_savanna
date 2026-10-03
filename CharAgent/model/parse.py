"""/chat/completions 响应解析纯函数 (不依赖网络, 可直接单测).

- 非流式成功响应: choices / message / finish_reason / usage 字段映射
- 错误响应体: extract_error_message 提取可读错误信息
  (httpx 裸调与 openai SDK 适配器共用, 保证错误语义一致)

字段缺失视为畸形响应并显式抛 ModelProtocolError, 供上层决定重试或纠错
(而非静默返回残缺结果).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from CharAgent.model.utils.errors import ModelErrorKind, ModelProtocolError
from CharAgent.model.utils.types import (
    FinishReason,
    ModelResponse,
    ModelToolCall,
    Usage,
)


def extract_error_message(body: str) -> str:
    """从错误响应体提取可读信息: 优先 OpenAI 风格 {"error": {"message"}}.

    非 JSON 错误体回退截断原文.
    """
    try:
        data = json.loads(body)
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    except (json.JSONDecodeError, AttributeError):
        pass
    return body[:300] or "(空响应体)"


# 上游报「输入超窗口」时各家措辞不一 (OpenAI 系多是 context_length_exceeded 这个
# code, 另一些把话写在 message 里: "maximum context length is N tokens"). 这张表是
# **唯一**一处靠字符串判语义的地方 —— 判完就只剩 kind, 上层不必再认识这些字.
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context length",
    "too many tokens",
    "reduce the length of the messages",
)


def classify_error(status_code: int, body: str) -> ModelErrorKind:
    """HTTP 错误 → 语义分类, 只为回答一个问题: **这份请求还救不救得回来**?

    「超窗口」救得回来 (压一次再发, 见 compaction 的 emergency); 其余 4xx 救不回来
    (重发多少次都一样). 5xx 与 429 是另一回事 —— 那是上游自己的状态, 归重试层.

    Args:
        status_code: 上游返回的状态码.
        body: **原始响应体**, 不是抠出来的 message —— 判据可能落在 code 那一层.

    Returns:
        ModelErrorKind: 分类结果; 认不出来就是 `UNKNOWN`.
    """
    if status_code == 429:
        return ModelErrorKind.RATE_LIMITED
    if 500 <= status_code < 600:
        return ModelErrorKind.SERVER
    if 400 <= status_code < 500:
        lowered = body.lower()
        if any(marker in lowered for marker in _CONTEXT_OVERFLOW_MARKERS):
            return ModelErrorKind.CONTEXT_OVERFLOW
        return ModelErrorKind.INVALID_REQUEST
    return ModelErrorKind.UNKNOWN


def parse_finish_reason(value: Any) -> FinishReason:
    """finish_reason 六值映射 (对齐官方枚举).

    未知取值视为协议畸形, 显式报错而非静默吞掉.
    """
    if isinstance(value, FinishReason):
        return value
    try:
        return FinishReason(str(value))
    except ValueError as exc:
        raise ModelProtocolError(f"未知 finish_reason: {value!r}") from exc


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """usage 子结构护栏: 非 dict (缺失 / null / 类型不符) 一律归空映射."""
    return value if isinstance(value, dict) else {}


def parse_usage(data: Mapping[str, Any] | None) -> Usage | None:
    """usage 字段映射; 响应不带 usage 时返回 None (流式未开 include_usage 的场景).

    reasoning / 缓存字段按官方层级读取 —— reasoning_tokens 在
    completion_tokens_details 下 (顶层无该字段); 缓存命中优先取顶层
    prompt_cache_hit_tokens, 缺失时回退 prompt_tokens_details.cached_tokens
    (官方声明两者同值).

    **未命中那一档在源头补齐** (2026-10-05 真机发现): OpenAI 系只报命中
    (`prompt_tokens_details.cached_tokens`), 不报未命中数 —— 拿上游给的两个数做一次
    减法就有了 (`未命中 = 输入总量 - 命中`). 补在**这里**而不是等到算钱那一步, 是为了
    让下游只看得到一份完整的账: 逐轮累加 (`accumulate_usage` -> 运行行那五列)、
    `runs.usage_by_model`、金额折算三处本来都要各自面对「这一档缺了怎么办」, 而历史上
    只有金额那一步会推 —— 推完钱是对的, 入库的数字却少了这一档, 于是真机上出现了
    「输入 7468 / 未命中 1000 / 命中 6233」(1000 + 6233 != 7468) 这种行: 金额按 1235
    收费, 账上只记了一半. 一份数据一处补齐, 后面谁都不必再猜.

    **推不出来就不补** (缺输入总量 / 缺命中 / 差是负数): None 仍是「上游没报」的意思,
    与 0 (报过、值就是零) 是两回事. 算钱那一步 (`db/cost.py` 的 `_fill_missing_tier`)
    保留同样的推导, 作为**老数据与非常规来源的兜底** —— 两条路对同一份数据给出同一个数.
    """
    if not data:
        return None
    completion_details = _as_mapping(data.get("completion_tokens_details"))
    cache_hit = data.get("prompt_cache_hit_tokens")
    if cache_hit is None:
        cache_hit = _as_mapping(data.get("prompt_tokens_details")).get("cached_tokens")
    miss = data.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = _derive_cache_miss(data.get("prompt_tokens"), cache_hit)
    return Usage(
        input_tokens=data.get("prompt_tokens"),
        output_tokens=data.get("completion_tokens"),
        total_tokens=data.get("total_tokens"),
        reasoning_tokens=completion_details.get("reasoning_tokens"),
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=miss,
    )


def _derive_cache_miss(input_tokens: object, cache_hit: object) -> int | None:
    """未命中 = 输入总量 - 命中; 两个数都是整数且差不负才补 (补不出来给 None).

    类型也在这里挡一下: 这两个值来自上游 JSON, 拿字符串做减法会抛 `TypeError` ——
    解析层不该因为一个畸形的 usage 就炸掉整次响应 (同 `_as_mapping` 那条态度).
    """
    for value in (input_tokens, cache_hit):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
    derived = input_tokens - cache_hit
    return derived if derived >= 0 else None


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
