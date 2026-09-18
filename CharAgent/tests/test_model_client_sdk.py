"""openai SDK 适配器单测: respx mock HTTP (SDK 底层走 httpx), 不触网.

覆盖检查项: 非流式 / 流式经 SDK 全链路正确解析, 错误映射
(SDK 异常层级 -> 与 httpx 适配器同语义的 ModelError, 429 / 5xx / 连接 /
超时为瞬态可重试, 其余 4xx 永久). SDK 与 httpx 的一致性契约见
test_model_contract.py.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import respx
from helpers import API_KEY, BASE_URL, CHAT_URL, sse_chunk, text_completion_json

from CharAgent.model import (
    FinishReason,
    ModelConfigError,
    ModelConnectionError,
    ModelStatusError,
    ModelTimeoutError,
    OpenAIChatModel,
)

MESSAGES: list[dict[str, str]] = [
    {"role": "user", "content": "查询订单 20260701123456 的状态"}
]


@pytest_asyncio.fixture
async def openai_model():
    """真实 SDK 适配器实例, 测试用 respx 拦截其底层 httpx 网络请求."""
    model = OpenAIChatModel(api_key=API_KEY, base_url=BASE_URL, model="deepseek-flash")
    yield model
    await model.aclose()


# ---------------------------------------------------------------------------
# 构造校验
# ---------------------------------------------------------------------------


def test_api_key_required_at_construction() -> None:
    """与 HttpXChatModel 同语义: 空 api_key 在构造时即报配置错误."""
    with pytest.raises(ModelConfigError, match="api_key"):
        OpenAIChatModel(api_key="")


# ---------------------------------------------------------------------------
# 非流式: SDK 全链路解析 (传输 / 反序列化由 SDK, 字段语义解析走公共层)
# ---------------------------------------------------------------------------


async def test_non_stream_parses_text_reasoning_and_usage(
    openai_model: OpenAIChatModel,
) -> None:
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json=text_completion_json())
        )
        response = await openai_model.generate(MESSAGES)
    assert response.content == "订单已发货"
    assert response.reasoning == "核对订单号"  # SDK extra 字段保留后经公共解析 (#11)
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert response.usage.total_tokens == 101
    assert response.model == "deepseek-flash"
    assert not response.has_tool_calls


async def test_non_stream_without_reasoning_field(
    openai_model: OpenAIChatModel,
) -> None:
    """非推理模型分支: 无 reasoning_content 字段解析不报错 (有/无两分支兼容 #11)."""
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json=text_completion_json(reasoning=None))
        )
        response = await openai_model.generate(MESSAGES)
    assert response.reasoning is None
    assert response.content == "订单已发货"


# ---------------------------------------------------------------------------
# 流式: SDK chunk 逐块累积为完整响应
# ---------------------------------------------------------------------------


async def test_stream_accumulates_content_and_usage(
    openai_model: OpenAIChatModel,
) -> None:
    """SDK 流式: chunk 累积 + 末块 usage (官方形态), 结果与非流式同构."""
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
                {
                    "model": "deepseek-flash",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 3,
                        "total_tokens": 23,
                        "completion_tokens_details": {"reasoning_tokens": 1},
                    },
                }
            ),
            "data: [DONE]\n\n",
        ]
    )
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, text=body))
        response = await openai_model.generate(MESSAGES, stream=True)
    assert response.content == "订单已发货"
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None
    assert response.usage.total_tokens == 23
    assert response.usage.reasoning_tokens == 1
    assert response.model == "deepseek-flash"


# ---------------------------------------------------------------------------
# 错误映射: SDK 异常层级 -> ModelError (瞬态 / 永久语义与 httpx 适配器一致)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (429, True), (500, True), (503, True)],
)
async def test_status_error_mapping(
    openai_model: OpenAIChatModel, status: int, retryable: bool
) -> None:
    """非 2xx: 429 / 5xx 瞬态可重试, 其余 4xx 永久 (SDK 抛 APIStatusError 子类)."""
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(
                status, json={"error": {"message": f"server says {status}"}}
            )
        )
        with pytest.raises(ModelStatusError) as exc_info:
            await openai_model.generate(MESSAGES)
    error = exc_info.value
    assert error.status_code == status
    assert error.retryable is retryable
    # 错误体 message 透出 (SDK 的 response 文本经公共 extract_error_message)
    assert f"server says {status}" in str(error)


async def test_connection_error_maps_to_transient(
    openai_model: OpenAIChatModel,
) -> None:
    """网络不可达: SDK 包装为 APIConnectionError -> ModelConnectionError 瞬态."""

    def raise_connect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with respx.mock() as router:
        router.post(CHAT_URL).mock(side_effect=raise_connect)
        with pytest.raises(ModelConnectionError) as exc_info:
            await openai_model.generate(MESSAGES)
    assert exc_info.value.retryable is True


async def test_timeout_maps_to_transient(openai_model: OpenAIChatModel) -> None:
    """超时: SDK 包装为 APITimeoutError -> ModelTimeoutError 瞬态."""

    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    async with respx.mock() as router:
        router.post(CHAT_URL).mock(side_effect=raise_timeout)
        with pytest.raises(ModelTimeoutError) as exc_info:
            await openai_model.generate(MESSAGES)
    assert exc_info.value.retryable is True
