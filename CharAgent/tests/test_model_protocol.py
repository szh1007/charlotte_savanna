"""模型层纯函数单元测试: 协议字段解析 (不触网).

覆盖检查项: tool_calls 结构 / reasoning 分离 / finish_reason 取值 /
usage 映射 / SSE delta 累积状态机 / 环境变量构建.
网络侧行为见 test_model_client_httpx.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from CharAgent.model import (
    FinishReason,
    ModelConfigError,
    ModelProtocolError,
    ModelResponse,
    ModelToolCall,
    chat_model_from_env,
    openai_chat_model_from_env,
)
from CharAgent.model.parse import (
    parse_chat_completion,
    parse_finish_reason,
    parse_tool_calls,
    parse_usage,
)
from CharAgent.model.stream import (
    StreamAccumulator,
    accumulate_delta,
    apply_sse_chunk,
    build_stream_response,
)
from CharAgent.model.utils.config import check_thinking_params, strip_provider_prefix

# ---------------------------------------------------------------------------
# 样本: 以真实 DeepSeek 响应结构为准 (非流式 message.reasoning_content / 流式 delta)
# ---------------------------------------------------------------------------


def completion_with_reasoning() -> dict[str, Any]:
    return {
        "id": "chatcmpl-test-001",
        "object": "chat.completion",
        "created": 1788850824,
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "订单已发货",
                    "reasoning_content": "用户查询物流, 先核对订单号",
                },
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 89, "completion_tokens": 12, "total_tokens": 101},
    }


def tool_calls_completion() -> dict[str, Any]:
    """并行 tool_calls: 同在一个 assistant message 保持并行语义 (#1)."""
    return {
        "id": "chatcmpl-test-002",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "query_order",
                                "arguments": '{"order_no": "20260701123456"}',
                            },
                        },
                        {
                            "id": "call_def456",
                            "type": "function",
                            "function": {
                                "name": "get_logistics",
                                "arguments": '{"order_no": "20260701123456","days":7}',
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260},
    }


# ---------------------------------------------------------------------------
# finish_reason 映射 (#10 官方六种取值 + 未知值畸形)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("stop", FinishReason.STOP),
        ("tool_calls", FinishReason.TOOL_CALLS),
        ("length", FinishReason.LENGTH),
        ("content_filter", FinishReason.CONTENT_FILTER),
        # 服务端中断两值: 官方枚举成员, 不是畸形 (曾漏枚举被误判为协议错误)
        ("insufficient_system_resource", FinishReason.INSUFFICIENT_SYSTEM_RESOURCE),
        ("aborted", FinishReason.ABORTED),
        (FinishReason.LENGTH, FinishReason.LENGTH),  # 已枚举值直接通过
    ],
)
def test_parse_finish_reason_maps_all_values(
    value: object, expected: FinishReason
) -> None:
    assert parse_finish_reason(value) is expected


@pytest.mark.parametrize("value", [None, "unknown_reason", 42])
def test_parse_finish_reason_rejects_unknown(value: object) -> None:
    """未知 finish_reason 视为协议畸形显式报错, 供上层判断而非静默吞掉."""
    with pytest.raises(ModelProtocolError):
        parse_finish_reason(value)


# ---------------------------------------------------------------------------
# tool_calls 结构解析
# ---------------------------------------------------------------------------


def test_parse_tool_calls_returns_empty_for_none() -> None:
    assert parse_tool_calls(None) == []


def test_parse_tool_calls_extracts_parallel_calls() -> None:
    """并行 tool_calls 结构: id / function.name / arguments 提取.

    arguments 保持原始 JSON 字符串 (畸形留给工具执行层纠错 #2).
    """
    calls = parse_tool_calls(
        tool_calls_completion()["choices"][0]["message"]["tool_calls"]
    )
    assert len(calls) == 2
    first, second = calls
    assert first == ModelToolCall(
        id="call_abc123", name="query_order", arguments='{"order_no": "20260701123456"}'
    )
    assert second.id == "call_def456"
    assert second.name == "get_logistics"
    # arguments 保真: 仍是可独立 json.loads 的原始字符串,
    # 畸形 JSON 由工具执行层给出可操作错误 (#2)
    assert isinstance(second.arguments, str)


def test_parse_tool_calls_rejects_missing_id() -> None:
    items = [
        {"type": "function", "function": {"name": "query_order", "arguments": "{}"}}
    ]
    with pytest.raises(ModelProtocolError, match="id"):
        parse_tool_calls(items)


def test_parse_tool_calls_rejects_missing_name() -> None:
    items = [{"id": "call_1", "type": "function", "function": {"arguments": "{}"}}]
    with pytest.raises(ModelProtocolError, match="name"):
        parse_tool_calls(items)


def test_parse_tool_calls_rejects_non_list() -> None:
    with pytest.raises(ModelProtocolError):
        parse_tool_calls({"id": "call_1"})


# ---------------------------------------------------------------------------
# usage 映射
# ---------------------------------------------------------------------------


def test_parse_usage_maps_openai_fields() -> None:
    usage = parse_usage(
        {"prompt_tokens": 89, "completion_tokens": 12, "total_tokens": 101}
    )
    assert usage is not None
    assert usage.input_tokens == 89
    assert usage.output_tokens == 12
    assert usage.total_tokens == 101
    assert usage.reasoning_tokens is None  # 非推理统计字段缺失时兼容 None (#11)
    assert usage.cache_hit_tokens is None
    assert usage.cache_miss_tokens is None


def test_parse_usage_reads_reasoning_tokens_from_nested_details() -> None:
    """reasoning_tokens 在 completion_tokens_details 下 (官方层级), 非顶层."""
    usage = parse_usage(
        {
            "prompt_tokens": 89,
            "completion_tokens": 30,
            "total_tokens": 119,
            "completion_tokens_details": {"reasoning_tokens": 18},
        }
    )
    assert usage is not None
    assert usage.reasoning_tokens == 18


def test_parse_usage_ignores_top_level_reasoning_tokens() -> None:
    """顶层 reasoning_tokens 非官方 schema 字段, 不再当数据源 (旧实现误读位置)."""
    usage = parse_usage(
        {
            "prompt_tokens": 89,
            "completion_tokens": 30,
            "total_tokens": 119,
            "reasoning_tokens": 18,
        }
    )
    assert usage is not None
    assert usage.reasoning_tokens is None


def test_parse_usage_reads_cache_hit_and_miss_tokens() -> None:
    """缓存计量: 顶层 prompt_cache_hit/miss_tokens 优先 (成本核算依赖命中折扣)."""
    usage = parse_usage(
        {
            "prompt_tokens": 89,
            "completion_tokens": 12,
            "total_tokens": 101,
            "prompt_cache_hit_tokens": 64,
            "prompt_cache_miss_tokens": 25,
        }
    )
    assert usage is not None
    assert usage.cache_hit_tokens == 64
    assert usage.cache_miss_tokens == 25


def test_parse_usage_falls_back_to_cached_tokens() -> None:
    """顶层命中字段缺失时回退 prompt_tokens_details.cached_tokens (官方声明同值)."""
    usage = parse_usage(
        {
            "prompt_tokens": 89,
            "completion_tokens": 12,
            "total_tokens": 101,
            "prompt_tokens_details": {"cached_tokens": 32},
        }
    )
    assert usage is not None
    assert usage.cache_hit_tokens == 32


@pytest.mark.parametrize("malformed", [{"completion_tokens_details": None}, "legacy"])
def test_parse_usage_tolerates_malformed_details(malformed: object) -> None:
    """usage 子结构类型不符 (null / 字符串) 归空映射, 不抛裸 TypeError."""
    data: dict[str, object] = {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }
    data["completion_tokens_details"] = malformed
    data["prompt_tokens_details"] = malformed
    usage = parse_usage(data)  # type: ignore[arg-type]
    assert usage is not None
    assert usage.reasoning_tokens is None
    assert usage.cache_hit_tokens is None


def test_parse_usage_returns_none_when_missing() -> None:
    assert parse_usage(None) is None
    assert parse_usage({}) is None


# ---------------------------------------------------------------------------
# 非流式完整响应解析
# ---------------------------------------------------------------------------


def test_parse_completion_text_and_reasoning_separated() -> None:
    """文本与 reasoning_content 分离 (#11): reasoning 单独字段, 不混入 content."""
    response = parse_chat_completion(completion_with_reasoning())
    assert response.content == "订单已发货"
    assert response.reasoning == "用户查询物流, 先核对订单号"
    assert response.finish_reason is FinishReason.STOP
    assert response.model == "deepseek-flash"
    assert not response.has_tool_calls
    assert response.usage is not None
    assert response.usage.total_tokens == 101
    assert response.raw is not None  # 原始响应保留, 供排查与录制回放样本 (#61)


def test_parse_completion_without_reasoning_field() -> None:
    """非推理模型无 reasoning_content 字段, 兼容分支: reasoning 为 None 不报错."""
    data = completion_with_reasoning()
    del data["choices"][0]["message"]["reasoning_content"]
    response = parse_chat_completion(data)
    assert response.reasoning is None
    assert response.content == "订单已发货"


def test_parse_completion_with_tool_calls() -> None:
    """tool_calls 响应: content 为 null, finish_reason 为 tool_calls (loop 继续分支)."""
    response = parse_chat_completion(tool_calls_completion())
    assert response.content is None
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.has_tool_calls
    assert [call.name for call in response.tool_calls] == [
        "query_order",
        "get_logistics",
    ]


@pytest.mark.parametrize(
    ("finish", "expected"),
    [("length", FinishReason.LENGTH), ("content_filter", FinishReason.CONTENT_FILTER)],
)
def test_parse_completion_truncation_finish_reasons(
    finish: str, expected: FinishReason
) -> None:
    """length / content_filter 两终止值正确解析 (loop 需据此走截断 / 拦截分支 #10)."""
    data = completion_with_reasoning()
    data["choices"][0]["finish_reason"] = finish
    response = parse_chat_completion(data)
    assert response.finish_reason is expected


def test_parse_completion_without_usage_tolerated() -> None:
    data = completion_with_reasoning()
    del data["usage"]
    assert parse_chat_completion(data).usage is None


@pytest.mark.parametrize(
    "corrupt",
    [
        {},  # 无 choices
        {"choices": []},  # 空 choices
        {"choices": "not-a-list"},  # choices 非列表
    ],
)
def test_parse_completion_rejects_missing_choices(corrupt: dict[str, Any]) -> None:
    with pytest.raises(ModelProtocolError, match="choices"):
        parse_chat_completion(corrupt)


def test_parse_completion_rejects_multimodal_content_array() -> None:
    """多模态 content 数组超出协议范围 (#52-54 属 P2), 视为畸形显式报错."""
    data = completion_with_reasoning()
    data["choices"][0]["message"]["content"] = [{"type": "text", "text": "hi"}]
    with pytest.raises(ModelProtocolError, match="content"):
        parse_chat_completion(data)


# ---------------------------------------------------------------------------
# SSE 流式 delta 累积 (累积为完整响应)
# ---------------------------------------------------------------------------


def test_stream_accumulates_content_and_reasoning_deltas() -> None:
    acc = StreamAccumulator()
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {"index": 0, "delta": {"content": "订单"}, "finish_reason": None}
            ]
        },
    )
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "已发货", "reasoning_content": "核对"},
                    "finish_reason": None,
                }
            ]
        },
    )
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"reasoning_content": "订单号"},
                    "finish_reason": None,
                }
            ]
        },
    )
    apply_sse_chunk(
        acc, {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    )
    response = build_stream_response(acc)
    assert response.content == "订单已发货"
    assert response.reasoning == "核对订单号"  # reasoning 增量与 content 分开累积
    assert response.finish_reason is FinishReason.STOP


def test_stream_tool_call_arguments_delta_concatenated() -> None:
    """tool_calls 的 arguments 是分片 delta, 按 index 拼接 JSON 而非整段返回 (#10)."""
    acc = StreamAccumulator()
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "query_order", "arguments": ""},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
    )
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '{"order_no": "'}}
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
    )
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '20260701123456"}'}}
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
    )
    apply_sse_chunk(
        acc, {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
    )
    response = build_stream_response(acc)
    assert response.has_tool_calls
    call = response.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "query_order"
    assert call.arguments == '{"order_no": "20260701123456"}'  # 三处分片拼成完整 JSON


def test_stream_parallel_tool_calls_ordered_by_index() -> None:
    """并行 tool_calls 流式按 index 分片交错到达, 组装后仍保持原始顺序."""
    acc = StreamAccumulator()
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_a",
                                "function": {"name": "tool_a", "arguments": "{}"},
                            },
                            {
                                "index": 1,
                                "id": "call_b",
                                "function": {"name": "tool_b", "arguments": "{}"},
                            },
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
    )
    apply_sse_chunk(
        acc, {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
    )
    response = build_stream_response(acc)
    assert [call.id for call in response.tool_calls] == ["call_a", "call_b"]


def test_stream_usage_attached_to_final_chunk() -> None:
    """usage 附着在末块上 (官方形态): 该块 delta 无内容且 finish_reason 非 null."""
    acc = StreamAccumulator()
    apply_sse_chunk(
        acc,
        {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]},
    )
    apply_sse_chunk(
        acc,
        {
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "", "role": None},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 3,
                "total_tokens": 23,
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
        },
    )
    response = build_stream_response(acc)
    assert response.finish_reason is FinishReason.STOP
    assert response.content == "ok"  # 末块空 delta 不污染正文
    assert response.usage is not None
    assert response.usage.total_tokens == 23
    assert response.usage.reasoning_tokens == 1
    assert response.model == "deepseek-flash"


def test_stream_null_usage_chunks_tolerated() -> None:
    """include_usage 下除末块外所有块 usage 均为 null, 不得覆盖已累积值."""
    acc = StreamAccumulator()
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {"index": 0, "delta": {"content": "ok"}, "finish_reason": None}
            ],
            "usage": None,
        },
    )
    apply_sse_chunk(
        acc,
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
        },
    )
    response = build_stream_response(acc)
    assert response.usage is not None and response.usage.total_tokens == 23


def test_stream_without_usage_chunk_tolerated() -> None:
    acc = StreamAccumulator()
    apply_sse_chunk(
        acc,
        {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]},
    )
    apply_sse_chunk(
        acc, {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    )
    assert build_stream_response(acc).usage is None


def test_streamaccumulate_delta_raises_on_tool_call_without_index() -> None:
    acc = StreamAccumulator()
    with pytest.raises(ModelProtocolError, match="index"):
        accumulate_delta(
            acc, {"tool_calls": [{"id": "call_1", "function": {"name": "x"}}]}
        )


def test_streamaccumulate_delta_rejects_non_string_content() -> None:
    """畸形 delta (多模态 content 数组) 抛 ModelProtocolError 而非裸 TypeError."""
    acc = StreamAccumulator()
    with pytest.raises(ModelProtocolError, match="content"):
        accumulate_delta(acc, {"content": [{"type": "text", "text": "hi"}]})


def test_streamaccumulate_delta_rejects_non_string_arguments() -> None:
    """arguments 分片 delta 非字符串 (畸形) 同样按协议错误报出."""
    acc = StreamAccumulator()
    with pytest.raises(ModelProtocolError, match="arguments"):
        accumulate_delta(
            acc,
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {
                            "name": "query_order",
                            "arguments": {"order_no": 1},
                        },
                    }
                ]
            },
        )


def test_build_stream_response_requires_finish_reason() -> None:
    """流结束仍无 finish_reason (断流) 视为畸形, 不返回残缺结果."""
    acc = StreamAccumulator()
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {"index": 0, "delta": {"content": "半截"}, "finish_reason": None}
            ]
        },
    )
    with pytest.raises(ModelProtocolError, match="finish_reason"):
        build_stream_response(acc)


def test_build_stream_response_rejects_incomplete_tool_call() -> None:
    acc = StreamAccumulator()
    apply_sse_chunk(
        acc,
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"name": "x"}}]},
                    "finish_reason": None,
                }
            ]
        },
    )
    apply_sse_chunk(
        acc, {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
    )
    with pytest.raises(ModelProtocolError, match="不完整"):
        build_stream_response(acc)


# ---------------------------------------------------------------------------
# ModelResponse 便捷属性
# ---------------------------------------------------------------------------


def test_model_response_has_tool_calls_property() -> None:
    assert not ModelResponse(content=None).has_tool_calls
    assert ModelResponse(
        content=None, tool_calls=[ModelToolCall(id="c", name="x", arguments="{}")]
    ).has_tool_calls


# ---------------------------------------------------------------------------
# 环境变量构建 (from_env)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("deepseek-flash", "deepseek-flash"),  # 裸名原样
        ("deepseek:deepseek-flash", "deepseek-flash"),  # LangChain 风格前缀剥离
    ],
)
def test_strip_provider_prefix(raw: str, expected: str) -> None:
    assert strip_provider_prefix(raw) == expected


# ---------------------------------------------------------------------------
# thinking / reasoning_effort 校验 (两条思考模式开关路径)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("thinking", "reasoning_effort"),
    [
        (None, None),
        (True, None),
        (None, "high"),
        (True, "high"),  # 同向: 都开
        (True, "max"),
        (True, "medium"),  # 兼容别名
        (False, None),
        (False, "none"),  # 同向: 都关
        (None, "none"),  # none 单独出现即关闭思考模式
    ],
)
def test_check_thinking_params_accepts_consistent(
    thinking: bool | None, reasoning_effort: str | None
) -> None:
    check_thinking_params(thinking, reasoning_effort)  # 不抛异常即通过


@pytest.mark.parametrize(
    ("thinking", "reasoning_effort"),
    [
        (False, "low"),  # disabled 与开启型 effort 矛盾
        (False, "high"),
        (False, "max"),
        (True, "none"),  # enabled 与 none (关闭型) 矛盾
    ],
)
def test_check_thinking_params_rejects_conflict(
    thinking: bool, reasoning_effort: str
) -> None:
    with pytest.raises(ModelConfigError, match="矛盾"):
        check_thinking_params(thinking, reasoning_effort)


@pytest.mark.parametrize("effort", ["hihg", "HIGH", "medium2", ""])
def test_check_thinking_params_rejects_unknown_effort(effort: str) -> None:
    """取值不在官方集合内直接报错 —— 拼写错误不会静默发给服务端."""
    with pytest.raises(ModelConfigError, match="reasoning_effort"):
        check_thinking_params(None, effort)


def test_chat_model_from_env_strips_langchain_prefix() -> None:
    """.env 中 DEEPSEEK_MODEL_NAME 是 LangChain 风格 deepseek:xxx, 裸调端点只收裸名."""
    model = chat_model_from_env(
        {
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_MODEL_NAME": "deepseek:deepseek-flash",
        }
    )
    assert model.model == "deepseek-flash"
    assert model.base_url == "https://api.deepseek.com"  # 未配置时用官方默认


def test_chat_model_from_env_explicit_overrides_win() -> None:
    model = chat_model_from_env(
        {"DEEPSEEK_API_KEY": "sk-test"},
        model="openai:gpt-test",
        base_url="https://example.com/v1",
    )
    assert model.model == "gpt-test"
    assert model.base_url == "https://example.com/v1"


def test_chat_model_from_env_raises_without_api_key() -> None:
    with pytest.raises(ModelConfigError, match="DEEPSEEK_API_KEY"):
        chat_model_from_env({})


def test_chat_model_from_env_explicit_empty_api_key_raises() -> None:
    """显式 api_key 为空串视为配置错误, 不静默回退 env (显式覆盖优先级语义)."""
    with pytest.raises(ModelConfigError, match="DEEPSEEK_API_KEY"):
        chat_model_from_env({"DEEPSEEK_API_KEY": "sk-env"}, api_key="")


async def test_both_from_env_factories_resolve_same_config() -> None:
    """双适配器工厂对同一 env 解析出相同 model / base_url (配置入口一致, 防 drift)."""
    env = {
        "DEEPSEEK_API_KEY": "sk-test",
        "DEEPSEEK_MODEL_NAME": "deepseek:deepseek-flash",
    }
    http_model = chat_model_from_env(env)
    sdk_model = openai_chat_model_from_env(env)
    try:
        assert http_model.model == sdk_model.model == "deepseek-flash"
        assert http_model.base_url == sdk_model.base_url == "https://api.deepseek.com"
        assert sdk_model.api_key == http_model.api_key == "sk-test"
    finally:
        await http_model.aclose()
        await sdk_model.aclose()
