"""双适配器契约测试 (issue 02 / #63): httpx 裸调 vs openai SDK.

对同一 mock wire 响应 (非流式 JSON / SSE 文本), 两适配器应产出等价的
ModelResponse —— 语义字段 (content / reasoning / finish_reason / tool_calls /
usage / model) 全等; raw 允许差异 (SDK model_dump 与 wire 有规范化差异).

同时约束请求侧: 对同一调用参数, 两适配器发出的请求体相等
(openai SDK 对显式 None 参数不剔除, 需与 client_httpx._resolve_payload 保持
「None 不携带」同语义, 此处防两处实现 drift).

网络由 respx 拦截, 不触网.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from helpers import (
    API_KEY,
    BASE_URL,
    CHAT_URL,
    TOOL_SCHEMA,
    sse_chunk,
    text_completion_json,
)

from CharAgent.model import (
    FinishReason,
    HttpXChatModel,
    ModelProtocolError,
    ModelResponse,
    OpenAIChatModel,
)

MESSAGES: list[dict[str, str]] = [
    {"role": "user", "content": "查询订单 20260701123456 的状态"}
]

# 契约对比的适配器对 (fixture 产出, 测试参数注解用)
ModelPair = tuple[HttpXChatModel, OpenAIChatModel]


# ---------------------------------------------------------------------------
# 基建: 同一 wire 响应分别喂两适配器
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def model_pair() -> ModelPair:
    """同一构造参数的 httpx 裸调 + openai SDK 实例 (契约对比基准)."""
    kwargs = {"api_key": API_KEY, "base_url": BASE_URL, "model": "deepseek-v4-flash"}
    http_model = HttpXChatModel(**kwargs)
    sdk_model = OpenAIChatModel(**kwargs)
    yield http_model, sdk_model
    await http_model.aclose()
    await sdk_model.aclose()


async def _fetch(
    adapter: HttpXChatModel | OpenAIChatModel,
    *,
    json_body: dict[str, Any] | None = None,
    stream_body: str | None = None,
    **gen_kwargs: Any,
) -> ModelResponse:
    """单适配器发一次 generate, 网络由 respx 拦截 (mock 响应为 json 或 SSE 文本)."""
    async with respx.mock() as router:
        if stream_body is not None:
            router.post(CHAT_URL).mock(
                return_value=httpx.Response(
                    200, text=stream_body, headers={"content-type": "text/event-stream"}
                )
            )
        else:
            router.post(CHAT_URL).mock(return_value=httpx.Response(200, json=json_body))
        return await adapter.generate(MESSAGES, **gen_kwargs)


async def _capture_request(
    adapter: HttpXChatModel | OpenAIChatModel,
    *,
    stream: bool = False,
    **gen_kwargs: Any,
) -> dict[str, Any]:
    """拦截一次 generate 调用, 返回实际发出的请求体 (验证请求侧契约)."""
    stream_body = None
    if stream:
        stream_body = "".join(
            [
                sse_chunk(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "ok"},
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
        route = router.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                text=stream_body,
                headers={"content-type": "text/event-stream"},
            )
            if stream_body is not None
            else httpx.Response(200, json=text_completion_json())
        )
        if stream:
            await adapter.generate(MESSAGES, stream=True, **gen_kwargs)
        else:
            await adapter.generate(MESSAGES, **gen_kwargs)
    return json.loads(route.calls.last.request.content)


def assert_same_response(a: ModelResponse, b: ModelResponse) -> None:
    """契约断言: 两适配器对同一输入产出语义字段全等的 ModelResponse.

    raw 不比较: httpx 保留原始 wire JSON, SDK 保留 model_dump 结果,
    两者存在允许的规范化差异 (如 role / logprobs 等非语义字段).
    """
    assert a.content == b.content
    assert a.reasoning == b.reasoning
    assert a.finish_reason is b.finish_reason
    assert a.tool_calls == b.tool_calls
    assert a.usage == b.usage
    assert a.model == b.model


# ---------------------------------------------------------------------------
# 请求体契约: 同一调用参数 -> 相同 wire 请求体
# ---------------------------------------------------------------------------


async def test_request_body_full_params_equivalent(model_pair: ModelPair) -> None:
    """完整参数: tools + 三个采样参数 -> 两适配器请求体逐字节同构.

    约束 SDK 侧不因 None / 序列化差异偏离 http 手拼的请求体.
    """
    http_model, sdk_model = model_pair
    kwargs = {"tools": [TOOL_SCHEMA], "temperature": 0.2, "top_p": 0.9, "seed": 42}
    http_body = await _capture_request(http_model, **kwargs)
    sdk_body = await _capture_request(sdk_model, **kwargs)
    assert http_body == sdk_body
    assert sdk_body["model"] == "deepseek-v4-flash"
    assert sdk_body["tools"] == [TOOL_SCHEMA]
    assert sdk_body["temperature"] == 0.2
    assert sdk_body["seed"] == 42
    assert sdk_body["stream"] is False
    assert "stream_options" not in sdk_body  # 非流式无 usage chunk 概念


async def test_request_body_defaults_equivalent(model_pair: ModelPair) -> None:
    """无 tools / 采样参数 -> 都只发 model + messages + stream.

    防 openai SDK 对 None 参数不剔除 (发 "tools": null) 的 drift.
    """
    http_model, sdk_model = model_pair
    http_body = await _capture_request(http_model)
    sdk_body = await _capture_request(sdk_model)
    assert http_body == sdk_body
    assert set(sdk_body) == {"model", "messages", "stream"}


async def test_request_body_stream_equivalent(model_pair: ModelPair) -> None:
    """流式请求: stream=true + include_usage, 两适配器一致."""
    http_model, sdk_model = model_pair
    http_body = await _capture_request(http_model, stream=True)
    sdk_body = await _capture_request(sdk_model, stream=True)
    assert http_body == sdk_body
    assert sdk_body["stream"] is True
    assert sdk_body["stream_options"] == {"include_usage": True}


# ---------------------------------------------------------------------------
# 响应契约: 非流式
# ---------------------------------------------------------------------------


async def test_non_stream_text_and_reasoning_equivalent(model_pair: ModelPair) -> None:
    """非流式 text + reasoning_content: SDK extra 字段保留并经同一解析映射.

    reasoning / usage / finish_reason / model 全字段等价.
    """
    http_model, sdk_model = model_pair
    body = text_completion_json()
    via_http = await _fetch(http_model, json_body=body)
    via_sdk = await _fetch(sdk_model, json_body=body)
    assert_same_response(via_http, via_sdk)
    assert via_sdk.reasoning == "核对订单号"
    assert via_sdk.content == "订单已发货"
    assert via_sdk.usage is not None
    assert via_sdk.usage.total_tokens == 101


async def test_non_stream_without_reasoning_equivalent(model_pair: ModelPair) -> None:
    """非推理模型分支 (无 reasoning_content): reasoning 同为 None 不报错."""
    http_model, sdk_model = model_pair
    body = text_completion_json(reasoning=None)
    via_http = await _fetch(http_model, json_body=body)
    via_sdk = await _fetch(sdk_model, json_body=body)
    assert_same_response(via_http, via_sdk)
    assert via_sdk.reasoning is None


async def test_non_stream_tool_calls_equivalent(model_pair: ModelPair) -> None:
    """并行 tool_calls: content null + finish tool_calls, arguments 保真等价."""
    http_model, sdk_model = model_pair
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
    via_http = await _fetch(http_model, json_body=body)
    via_sdk = await _fetch(sdk_model, json_body=body)
    assert_same_response(via_http, via_sdk)
    assert via_sdk.finish_reason is FinishReason.TOOL_CALLS
    assert via_sdk.has_tool_calls
    assert [call.name for call in via_sdk.tool_calls] == [
        "query_order",
        "get_logistics",
    ]
    # arguments 保真: SDK 不透传解析后的对象, 仍是可独立 json.loads 的原始字符串
    assert via_sdk.tool_calls[0].arguments == '{"order_no": "20260701123456"}'


@pytest.mark.parametrize("finish", ["length", "content_filter"])
async def test_non_stream_other_finish_reasons_equivalent(
    model_pair: ModelPair, finish: str
) -> None:
    """length / content_filter 终止语义 (截断 / 拦截分支) 双端一致."""
    http_model, sdk_model = model_pair
    body = text_completion_json()
    body["choices"][0]["finish_reason"] = finish
    via_http = await _fetch(http_model, json_body=body)
    via_sdk = await _fetch(sdk_model, json_body=body)
    assert_same_response(via_http, via_sdk)


# ---------------------------------------------------------------------------
# 响应契约: 流式 (SDK chunk 累积结果与 httpx 手解 SSE 等价)
# ---------------------------------------------------------------------------


def _stream_body_text() -> str:
    """内容分片 + reasoning 分片交错 + usage 收尾 chunk 的完整 SSE 流."""
    return "".join(
        [
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": ""},
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
                            "delta": {"content": "订单"},
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
                            "delta": {"content": "已发货"},
                            "finish_reason": None,
                        }
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


async def test_stream_content_and_reasoning_equivalent(model_pair: ModelPair) -> None:
    """流式 delta 累积: SDK 端 chunk 累积与非流式同构, 与 httpx 结果全等."""
    http_model, sdk_model = model_pair
    body = _stream_body_text()
    via_http = await _fetch(http_model, stream_body=body, stream=True)
    via_sdk = await _fetch(sdk_model, stream_body=body, stream=True)
    assert_same_response(via_http, via_sdk)
    assert via_sdk.content == "订单已发货"
    assert via_sdk.reasoning == "先核对订单号"  # reasoning 增量单独累积 (#11)
    assert via_sdk.finish_reason is FinishReason.STOP
    assert via_sdk.usage is not None
    assert via_sdk.usage.total_tokens == 23  # usage-only chunk 收尾累积
    assert via_sdk.model == "deepseek-v4-flash"


async def test_stream_tool_calls_fragments_equivalent(model_pair: ModelPair) -> None:
    """流式 tool_calls 分片 (id / name 首块, arguments 分片增量) 双端等价."""
    http_model, sdk_model = model_pair
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
                                        "function": {"arguments": '{"order_no": "'},
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
                                        "function": {"arguments": '20260701123456"}'},
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
    via_http = await _fetch(http_model, stream_body=body, stream=True)
    via_sdk = await _fetch(sdk_model, stream_body=body, stream=True)
    assert_same_response(via_http, via_sdk)
    call = via_sdk.tool_calls[0]
    assert call.id == "call_stream_1"
    assert call.arguments == '{"order_no": "20260701123456"}'
    assert via_sdk.finish_reason is FinishReason.TOOL_CALLS


# ---------------------------------------------------------------------------
# 畸形响应: 两实现报错语义一致 (结构畸形 + 非法 JSON, 均为永久错误)
# ---------------------------------------------------------------------------


async def test_malformed_structure_raises_protocol_error_on_both(
    model_pair: ModelPair,
) -> None:
    """choices 为空列表: 双端都映射为 ModelProtocolError (畸形 = 永久错误).

    实测该输入能被 SDK 宽松解析 (choices 为可选字段), 错误实际由共享解析层
    parse_chat_completion 抛出, 而非 SDK schema 校验 —— 双端报错源头一致.
    """
    http_model, sdk_model = model_pair
    body = text_completion_json()
    body["choices"] = []
    with pytest.raises(ModelProtocolError) as http_err:
        await _fetch(http_model, json_body=body)
    with pytest.raises(ModelProtocolError) as sdk_err:
        await _fetch(sdk_model, json_body=body)
    assert http_err.value.retryable is False
    assert sdk_err.value.retryable is False


@pytest.mark.parametrize("content_type", ["application/json", "text/html"])
async def test_malformed_non_json_body_raises_on_both(
    model_pair: ModelPair, content_type: str
) -> None:
    """2xx 但 body 不是合法 JSON: 双端都映射 ModelProtocolError (review B-1).

    差异路径 (此前 SDK 泄漏裸异常): SDK 对 json content-type 的坏 body 抛裸
    json.JSONDecodeError, 对非 json content-type 宽松返回原始文本 (str) ——
    两分支都在适配器层收敛为畸形语义; httpx 端 response.json() 直接解析失败.
    """
    http_model, sdk_model = model_pair
    malformed = httpx.Response(
        200,
        text="<html>这不是合法 JSON</html>",
        headers={"content-type": content_type},
    )
    with pytest.raises(ModelProtocolError) as http_err:
        async with respx.mock() as router:
            router.post(CHAT_URL).mock(return_value=malformed)
            await http_model.generate(MESSAGES)
    with pytest.raises(ModelProtocolError) as sdk_err:
        async with respx.mock() as router:
            router.post(CHAT_URL).mock(return_value=malformed)
            await sdk_model.generate(MESSAGES)
    assert http_err.value.retryable is False
    assert sdk_err.value.retryable is False


async def test_stream_malformed_line_raises_on_both(model_pair: ModelPair) -> None:
    """SSE 流内坏 JSON 行: 双端都映射 ModelProtocolError (畸形容错语义一致).

    httpx: 手解行 json.loads 失败; SDK: 迭代解析坏行抛裸 JSONDecodeError,
    适配器层统一映射 (review B-1/B-3).
    """
    http_model, sdk_model = model_pair
    body = "".join(
        [
            sse_chunk(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "订单"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            "data: 不是合法JSON\n\n",
            "data: [DONE]\n\n",
        ]
    )
    with pytest.raises(ModelProtocolError) as http_err:
        await _fetch(http_model, stream_body=body, stream=True)
    with pytest.raises(ModelProtocolError) as sdk_err:
        await _fetch(sdk_model, stream_body=body, stream=True)
    assert http_err.value.retryable is False
    assert sdk_err.value.retryable is False
