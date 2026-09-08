"""httpx 适配器集成层测试: respx mock HTTP, 不触网.

覆盖 issue 01 检查项: 请求 wire 格式 / 非流式解析 / SSE 流式 delta 累积 / 错误语义
(非 2xx 映射为明确异常, 429/5xx 瞬态可重试, 其余 4xx 永久).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from helpers import API_KEY, CHAT_URL, TOOL_SCHEMA

from CharAgent.model import (
    FinishReason,
    HttpXChatModel,
    ModelConfigError,
    ModelConnectionError,
    ModelProtocolError,
    ModelStatusError,
    ModelTimeoutError,
)

MESSAGES: list[dict[str, str]] = [
    {"role": "user", "content": "查询订单 20260701123456 的状态"}
]


def text_completion_json(*, reasoning: str | None = "核对订单号") -> dict[str, Any]:
    """贴近真实 DeepSeek 的非流式响应结构 (reasoning_content 可选, 默认带推理字段)."""
    message: dict[str, Any] = {"role": "assistant", "content": "订单已发货"}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "id": "chatcmpl-mock-001",
        "object": "chat.completion",
        "model": "deepseek-v4-flash",
        "choices": [
            {"index": 0, "message": message, "logprobs": None, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 89, "completion_tokens": 12, "total_tokens": 101},
    }


def sse_chunk(payload: dict[str, Any]) -> str:
    """单行 SSE data 块 (OpenAI 流式约定)."""
    return f"data: {json.dumps(payload)}\n\n"


def stream_payload(content_parts: list[str]) -> str:
    body = [
        sse_chunk(
            {
                "choices": [
                    {"index": 0, "delta": {"content": part}, "finish_reason": None}
                ]
            }
        )
        for part in content_parts
    ]
    body.append(
        sse_chunk({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    )
    body.append("data: [DONE]\n\n")
    return "".join(body)


# ---------------------------------------------------------------------------
# 非流式: 请求 wire 格式
# ---------------------------------------------------------------------------


async def test_non_stream_request_wire_format(chat_model: HttpXChatModel) -> None:
    """请求体与 OpenAI 兼容契约一致: model / messages / tools / temperature / top_p /
    seed.

    stream 显式为 false.
    """
    async with respx.mock() as router:
        route = router.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json=text_completion_json())
        )
        await chat_model.generate(
            MESSAGES,
            tools=[TOOL_SCHEMA],
            temperature=0.2,
            top_p=0.9,
            seed=42,
        )
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    payload = json.loads(request.content)
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["messages"] == MESSAGES
    assert payload["tools"] == [TOOL_SCHEMA]
    assert payload["stream"] is False
    assert payload["temperature"] == 0.2
    assert payload["top_p"] == 0.9
    assert payload["seed"] == 42
    assert "stream_options" not in payload  # 非流式无 usage chunk 概念


async def test_non_stream_request_defaults_omit_optional_fields(
    chat_model: HttpXChatModel,
) -> None:
    """temperature/top_p/seed/tools 均未配置时不携带多余键, 走服务端默认."""
    async with respx.mock() as router:
        route = router.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json=text_completion_json())
        )
        await chat_model.generate(MESSAGES)
    payload = json.loads(route.calls.last.request.content)
    assert set(payload) == {"model", "messages", "stream"}


# ---------------------------------------------------------------------------
# 非流式: 响应解析
# ---------------------------------------------------------------------------


async def test_non_stream_parses_text_and_usage(chat_model: HttpXChatModel) -> None:
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json=text_completion_json())
        )
        response = await chat_model.generate(MESSAGES)
    assert response.content == "订单已发货"
    assert response.reasoning == "核对订单号"  # reasoning_content 与正文分离 (#11)
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert response.usage.input_tokens == 89
    assert response.usage.total_tokens == 101
    assert response.model == "deepseek-v4-flash"
    assert not response.has_tool_calls


async def test_non_stream_tool_calls_response(chat_model: HttpXChatModel) -> None:
    """非流式 tool_calls 响应: content null, finish tool_calls, arguments 保真."""
    body = {
        "id": "chatcmpl-mock-002",
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_parallel_1",
                            "type": "function",
                            "function": {
                                "name": "query_order",
                                "arguments": '{"order_no": "20260701123456"}',
                            },
                        },
                        {
                            "id": "call_parallel_2",
                            "type": "function",
                            "function": {
                                "name": "get_logistics",
                                "arguments": '{"order_no": "20260701123456"}',
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 200, "completion_tokens": 60, "total_tokens": 260},
    }
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, json=body))
        response = await chat_model.generate(MESSAGES)
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.has_tool_calls
    assert [call.name for call in response.tool_calls] == [
        "query_order",
        "get_logistics",
    ]
    assert response.tool_calls[0].arguments == '{"order_no": "20260701123456"}'


async def test_non_stream_without_reasoning_field(chat_model: HttpXChatModel) -> None:
    """非推理模型分支: 无 reasoning_content 字段解析不报错 (有/无两分支兼容 #11)."""
    body = text_completion_json(reasoning=None)
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, json=body))
        response = await chat_model.generate(MESSAGES)
    assert response.reasoning is None
    assert response.content == "订单已发货"


@pytest.mark.parametrize(
    ("finish", "expected"),
    [("length", FinishReason.LENGTH), ("content_filter", FinishReason.CONTENT_FILTER)],
)
async def test_non_stream_other_finish_reasons(
    chat_model: HttpXChatModel, finish: str, expected: FinishReason
) -> None:
    """length / content_filter 经 HTTP 全链路正确解析 (#10 截断与拦截语义)."""
    body = text_completion_json()
    body["choices"][0]["finish_reason"] = finish
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, json=body))
        response = await chat_model.generate(MESSAGES)
    assert response.finish_reason is expected


# ---------------------------------------------------------------------------
# 流式: SSE delta 累积为非流式同构的完整响应
# ---------------------------------------------------------------------------


async def test_stream_accumulates_content_into_full_response(
    chat_model: HttpXChatModel,
) -> None:
    """流式 chunk 增量累积为完整响应: 与"非流式同构"是双适配器契约的前提."""
    async with respx.mock() as router:
        route = router.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                text=stream_payload(["订单", "已发货", ", 预计明天到达"]),
                headers={"content-type": "text/event-stream"},
            )
        )
        response = await chat_model.generate(MESSAGES, stream=True)
    # 流式请求带 stream=true 与 include_usage (收尾 usage chunk)
    payload = json.loads(route.calls.last.request.content)
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    # 累积结果: 三段 content 拼接
    assert response.content == "订单已发货, 预计明天到达"
    assert response.finish_reason is FinishReason.STOP
    assert not response.has_tool_calls


async def test_stream_accumulates_reasoning_delta(chat_model: HttpXChatModel) -> None:
    """reasoning_content 流式增量单独累积 (#11), 不与正文混拼."""
    body = "".join(
        [
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_content": "先核对订"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_content": "单号"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "订单已发货"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse_chunk(
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            ),
            "data: [DONE]\n\n",
        ]
    )
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        response = await chat_model.generate(MESSAGES, stream=True)
    assert response.reasoning == "先核对订单号"
    assert response.content == "订单已发货"


async def test_stream_tool_calls_delta_accumulation(chat_model: HttpXChatModel) -> None:
    """流式 tool_calls: id / name 首 chunk 给, arguments 分片增量累积.

    结果与非流式同构 (#10).
    """
    body = "".join(
        [
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_stream_1",
                                        "type": "function",
                                        "function": {
                                            "name": "query_order",
                                            "arguments": "",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '{"order_no": "2026'},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '0701123456"}'},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse_chunk(
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
            ),
            "data: [DONE]\n\n",
        ]
    )
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        response = await chat_model.generate(MESSAGES, stream=True)
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.has_tool_calls
    call = response.tool_calls[0]
    assert call.id == "call_stream_1"
    assert call.name == "query_order"
    assert call.arguments == '{"order_no": "20260701123456"}'


async def test_stream_usage_only_chunk_is_attached(chat_model: HttpXChatModel) -> None:
    """include_usage 收尾 chunk (choices 空) 累积到 usage.

    服务端不支持时 usage 保持 None.
    """
    body = "".join(
        [
            sse_chunk(
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "ok"}, "finish_reason": None}
                    ]
                }
            ),
            sse_chunk(
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            ),
            sse_chunk(
                {
                    "model": "deepseek-v4-flash",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 3,
                        "total_tokens": 23,
                    },
                }
            ),
            "data: [DONE]\n\n",
        ]
    )
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        response = await chat_model.generate(MESSAGES, stream=True)
    assert response.usage is not None
    assert response.usage.total_tokens == 23
    assert response.model == "deepseek-v4-flash"


async def test_stream_malformed_chunk_raises_protocol_error(
    chat_model: HttpXChatModel,
) -> None:
    body = sse_chunk(
        {"choices": [{"index": 0, "delta": {"content": "半截"}, "finish_reason": None}]}
    )
    body += "data: 不是JSON\n\n"
    body += "data: [DONE]\n\n"
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        with pytest.raises(ModelProtocolError, match="JSON"):
            await chat_model.generate(MESSAGES, stream=True)


async def test_stream_missing_finish_reason_raises_protocol_error(
    chat_model: HttpXChatModel,
) -> None:
    """流正常收尾但缺 finish_reason (断流) -> 畸形报错, 不返回残缺结果."""
    # 显式构造: 内容 chunk finish_reason 为 null, 直接 [DONE] 结束
    body = "".join(
        [
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "内容没有结尾信号"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            "data: [DONE]\n\n",
        ]
    )
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        with pytest.raises(ModelProtocolError, match="finish_reason"):
            await chat_model.generate(MESSAGES, stream=True)


# ---------------------------------------------------------------------------
# 错误语义: 非 2xx -> 明确异常 (供 retry 判断瞬态 / 永久)
# ---------------------------------------------------------------------------


async def test_http_400_maps_to_permanent_error(chat_model: HttpXChatModel) -> None:
    """4xx (非 429) 为永久错误: retryable=False (模型名非法/参数错误等, 重试无意义)."""
    body = {
        "error": {"message": "模型 deepseek:v4 不存在", "type": "invalid_request_error"}
    }
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(400, json=body))
        with pytest.raises(ModelStatusError) as exc_info:
            await chat_model.generate(MESSAGES)
    error = exc_info.value
    assert error.status_code == 400
    assert error.retryable is False
    assert "模型 deepseek:v4 不存在" in str(error)  # 错误体 message 透出, 供纠错参考


async def test_http_429_maps_to_transient_error(chat_model: HttpXChatModel) -> None:
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(
                429, json={"error": {"message": "rate limit exceeded"}}
            )
        )
        with pytest.raises(ModelStatusError) as exc_info:
            await chat_model.generate(MESSAGES)
    assert exc_info.value.status_code == 429
    assert exc_info.value.retryable is True


async def test_http_500_maps_to_transient_error(chat_model: HttpXChatModel) -> None:
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(500, text="internal error")
        )
        with pytest.raises(ModelStatusError) as exc_info:
            await chat_model.generate(MESSAGES)
    assert exc_info.value.status_code == 500
    assert exc_info.value.retryable is True


async def test_http_error_non_json_body_still_readable(
    chat_model: HttpXChatModel,
) -> None:
    """错误体非 JSON 时回退截断原文, 不因解析失败二次报错."""
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(502, text="bad gateway from proxy")
        )
        with pytest.raises(ModelStatusError) as exc_info:
            await chat_model.generate(MESSAGES)
    assert exc_info.value.status_code == 502
    assert exc_info.value.retryable is True
    assert "bad gateway from proxy" in str(exc_info.value)


async def test_connection_error_maps_to_transient(
    chat_model: HttpXChatModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网络不可达为瞬态错误 (retryable=True)."""

    async def raise_connect(*args: object, **kwargs: object) -> None:
        raise httpx.ConnectError("connection refused", request=None)

    monkeypatch.setattr(chat_model._client, "post", raise_connect)
    with pytest.raises(ModelConnectionError) as exc_info:
        await chat_model.generate(MESSAGES)
    assert exc_info.value.retryable is True
    assert isinstance(exc_info.value, ModelConnectionError)


async def test_timeout_maps_to_transient(
    chat_model: HttpXChatModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超时为瞬态错误 (retryable=True), 独立于连接错误可区分."""

    async def raise_timeout(*args: object, **kwargs: object) -> None:
        raise httpx.ReadTimeout("read timed out", request=None)

    monkeypatch.setattr(chat_model._client, "post", raise_timeout)
    with pytest.raises(ModelTimeoutError) as exc_info:
        await chat_model.generate(MESSAGES)
    assert exc_info.value.retryable is True


def test_api_key_required_at_construction() -> None:
    with pytest.raises(ModelConfigError, match="api_key"):
        HttpXChatModel(api_key="")
