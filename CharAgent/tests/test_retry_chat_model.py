"""模型重试包装测试 (difficulties #13): ChatModel 协议包装.

场景 → 断言 (两层: respx 拦真实适配器跑错误语义, 最小替身验参数透传):
- 429 后成功: 重试一次即返回; 请求体逐参数透传 (temperature / top_p / seed /
  max_tokens / thinking / reasoning_effort, 采样参数每次尝试一致 #68)
- 4xx (400): 立即上抛不重试 (永久错误, 重试一百次也一样)
- 5xx 重试耗尽: 抛**最后一个** ModelStatusError (retryable 保真, 供上层判断)
- 连接超时: 瞬态, 重试
- 上游中断 (finish_reason=insufficient_system_resource): 官方指引「稍后重试」
  -> 重试; 那次响应**已经计费**, 原样进 on_retry 的记录单 (#13);
  aborted 语义含糊 (可能是用户侧中断) -> 不重试, 原样返回给 loop 如实上报;
  开关关闭时同样原样返回
- 参数透传: 每次尝试的调用参数与首次完全一致 (轨迹断言)
- aclose 委托底层模型 (包装不改变生命周期语义)

与 loop 的组合 (接线证据, 见第三段): 模型层重试对 loop 透明 —— 429 后成功仍
以 final 收尾; 重试耗尽则异常上抛且**不发终局事件** (框架层异常契约).

睡眠 / 时钟注入 (tests/doubles.py), 零真实等待 (#61).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from doubles import FakeClock, RecordingSleep
from helpers import CHAT_URL, TOOL_SCHEMA, text_completion_json

from CharAgent.agent import AgentLoop, LoopOutcome
from CharAgent.model import (
    FinishReason,
    HttpXChatModel,
    ModelMessage,
    ModelResponse,
    ModelStatusError,
)
from CharAgent.retry import RetryAttempt, RetryingChatModel, RetryPolicy
from CharAgent.stream import EventType, StreamEvent

MESSAGES: list[ModelMessage] = [
    {"role": "user", "content": "我的订单 20260701123456 到哪了"}
]


def _policy(**overrides: object) -> RetryPolicy:
    """默认测试策略: 不抖动 + 注入时钟与记录式睡眠 (确定性, 零真实等待)."""
    kwargs: dict[str, object] = {
        "jitter": 0.0,
        "initial_delay": 0.5,
        "multiplier": 2.0,
        "max_attempts": 3,
        "max_elapsed_seconds": 60.0,
        "sleep": RecordingSleep(),  # 默认不真等待 (要断言等待时长时由用例覆盖)
    }
    kwargs.update(overrides)
    return RetryPolicy(**kwargs)  # type: ignore[arg-type]


def _interrupted_body(total_tokens: int = 101) -> dict[str, Any]:
    """上游中断样本: finish_reason=insufficient_system_resource (半截 + 已计费)."""
    body = text_completion_json(reasoning=None)
    body["choices"][0]["finish_reason"] = "insufficient_system_resource"
    body["choices"][0]["message"]["content"] = "您的订单"  # 半截
    body["usage"] = {
        "prompt_tokens": 89,
        "completion_tokens": total_tokens - 89,
        "total_tokens": total_tokens,
    }
    return body


class _FakeModel:
    """最小 ChatModel 替身: 记录每次调用的参数, 记录 aclose (透传断言用).

    attributes:
        calls: 每次 generate 的实参记录 (轨迹断言 #62 的数据源).
        closed: 是否被 aclose 过.
    """

    def __init__(self, *responses: ModelResponse) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        # 浅拷贝冻结「模型这次看到什么」: 否则后续 append 会污染先前轮次的轨迹
        # (与 mock_llm.ScriptedModel 同一防坑, #62)
        self.calls.append({"messages": list(messages), "tools": tools, **kwargs})
        return self._responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# 异常路径 (respx 拦真实适配器)
# ---------------------------------------------------------------------------


async def test_retries_429_then_succeeds(chat_model: HttpXChatModel) -> None:
    """429 后重试成功: 两次请求, 请求体逐参数透传 (#68 采样参数)."""
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    model = RetryingChatModel(
        chat_model, policy=_policy(sleep=sleep, time_source=clock)
    )

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(
            side_effect=[
                httpx.Response(429, json={"error": {"message": "rate limit exceeded"}}),
                httpx.Response(200, json=text_completion_json(reasoning=None)),
            ]
        )
        response = await model.generate(
            MESSAGES,
            [TOOL_SCHEMA],
            temperature=0.0,
            top_p=0.95,
            seed=7,
            max_tokens=100,
            thinking=True,
            reasoning_effort="low",
        )

    assert response.content == "订单已发货"
    assert len(route.calls) == 2
    assert sleep.delays == [0.5]
    payload = json.loads(route.calls[-1].request.content)
    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 0.95
    assert payload["seed"] == 7
    assert payload["max_tokens"] == 100
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "low"
    assert payload["tools"] == [TOOL_SCHEMA]


async def test_gives_up_on_400_without_retrying(chat_model: HttpXChatModel) -> None:
    """400 永久错误: 立即上抛, 只发一次请求 (#13 4xx 放弃)."""
    sleep = RecordingSleep()
    model = RetryingChatModel(chat_model, policy=_policy(sleep=sleep))

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(
            return_value=httpx.Response(400, json={"error": {"message": "bad request"}})
        )
        with pytest.raises(ModelStatusError) as exc_info:
            await model.generate(MESSAGES)

    assert exc_info.value.status_code == 400
    assert exc_info.value.retryable is False
    assert len(route.calls) == 1
    assert sleep.delays == []


async def test_exhausts_attempts_and_raises_last_error(
    chat_model: HttpXChatModel,
) -> None:
    """5xx 重试耗尽: 抛**最后一个** ModelStatusError (retryable 保真)."""
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    model = RetryingChatModel(
        chat_model, policy=_policy(sleep=sleep, time_source=clock, max_attempts=2)
    )

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(
            side_effect=[
                httpx.Response(500, text="internal error"),
                httpx.Response(503, text="service unavailable"),
            ]
        )
        with pytest.raises(ModelStatusError) as exc_info:
            await model.generate(MESSAGES)

    assert exc_info.value.status_code == 503  # 最后一次的错, 不是第一次的 500
    assert exc_info.value.retryable is True
    assert len(route.calls) == 2
    assert sleep.delays == [0.5]


async def test_retries_connection_timeout(chat_model: HttpXChatModel) -> None:
    """连接超时属瞬态: 等待后重试 (#13 超时重试)."""
    sleep = RecordingSleep()
    model = RetryingChatModel(chat_model, policy=_policy(sleep=sleep))

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(
            side_effect=[
                httpx.ConnectTimeout("连接超时"),
                httpx.Response(200, json=text_completion_json(reasoning=None)),
            ]
        )
        response = await model.generate(MESSAGES)

    assert response.content == "订单已发货"
    assert len(route.calls) == 2
    assert sleep.delays == [0.5]


async def test_retries_connection_error(chat_model: HttpXChatModel) -> None:
    """连接失败 (网络不可达) 同属瞬态: 等待后重试 (#13 网络故障重试)."""
    sleep = RecordingSleep()
    model = RetryingChatModel(chat_model, policy=_policy(sleep=sleep))

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(
            side_effect=[
                httpx.ConnectError("connection refused"),
                httpx.Response(200, json=text_completion_json(reasoning=None)),
            ]
        )
        response = await model.generate(MESSAGES)

    assert response.content == "订单已发货"
    assert len(route.calls) == 2
    assert sleep.delays == [0.5]


# ---------------------------------------------------------------------------
# 响应不合格路径 (上游中断: 已计费, 重试即再烧一次 #13)
# ---------------------------------------------------------------------------


async def test_retries_upstream_interrupted_response(
    chat_model: HttpXChatModel,
) -> None:
    """资源不足 (官方指引稍后重试) -> 重试; 被丢弃那次已计费, 原样进记录单."""
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    attempts: list[RetryAttempt] = []
    model = RetryingChatModel(
        chat_model,
        policy=_policy(sleep=sleep, time_source=clock),
        on_retry=[attempts.append],
    )

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(
            side_effect=[
                httpx.Response(200, json=_interrupted_body(total_tokens=101)),
                httpx.Response(200, json=text_completion_json(reasoning=None)),
            ]
        )
        response = await model.generate(MESSAGES)

    assert response.content == "订单已发货"
    assert len(route.calls) == 2
    assert sleep.delays == [0.5]
    (record,) = attempts
    assert record.error is None
    assert "上游中断" in record.reason
    # 被重试丢弃的那次用量随记录单交回调用方 (据此记账, #13)
    interrupted = record.result
    assert isinstance(interrupted, ModelResponse)
    assert interrupted.usage is not None
    assert interrupted.usage.total_tokens == 101


async def test_aborted_response_is_not_retried(chat_model: HttpXChatModel) -> None:
    """aborted 语义含糊 (可能是用户侧中断): 不重试, 原样返回给 loop 如实上报."""
    sleep = RecordingSleep()
    model = RetryingChatModel(chat_model, policy=_policy(sleep=sleep))
    body = text_completion_json(reasoning=None)
    body["choices"][0]["finish_reason"] = "aborted"

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(return_value=httpx.Response(200, json=body))
        response = await model.generate(MESSAGES)

    assert response.finish_reason is FinishReason.ABORTED
    assert len(route.calls) == 1
    assert sleep.delays == []


async def test_upstream_interrupted_retry_can_be_disabled(
    chat_model: HttpXChatModel,
) -> None:
    """关闭开关: 中断响应原样返回 (预算记账 / 缓存要「不再烧一次」时用)."""
    sleep = RecordingSleep()
    model = RetryingChatModel(
        chat_model, policy=_policy(sleep=sleep), retry_upstream_interrupted=False
    )

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(return_value=httpx.Response(200, json=_interrupted_body()))
        response = await model.generate(MESSAGES)

    assert response.finish_reason is FinishReason.INSUFFICIENT_SYSTEM_RESOURCE
    assert len(route.calls) == 1
    assert sleep.delays == []


# ---------------------------------------------------------------------------
# 参数透传与生命周期 (最小替身, 不经 HTTP)
# ---------------------------------------------------------------------------


async def test_forwards_every_generate_argument() -> None:
    """参数透传: 每次尝试的调用参数与首次完全一致 (包装不吞不改任何参数)."""
    inner = _FakeModel(
        ModelResponse(
            content="第一次半截",
            finish_reason=FinishReason.INSUFFICIENT_SYSTEM_RESOURCE,
        ),
        ModelResponse(content="第二次完整"),
    )
    model = RetryingChatModel(inner, policy=_policy())

    response = await model.generate(
        MESSAGES,
        [TOOL_SCHEMA],
        temperature=0.2,
        top_p=1.0,
        seed=42,
        max_tokens=8,
        thinking=False,
        reasoning_effort=None,
        stream=False,
    )

    assert response.content == "第二次完整"
    assert len(inner.calls) == 2
    assert inner.calls[0] == inner.calls[1]  # 每次尝试参数一致
    assert inner.calls[0]["messages"] == MESSAGES
    assert inner.calls[0]["tools"] == [TOOL_SCHEMA]
    assert inner.calls[0]["seed"] == 42
    assert inner.calls[0]["max_tokens"] == 8
    assert inner.calls[0]["stream"] is False


async def test_aclose_delegates_to_wrapped_model() -> None:
    """aclose 委托底层模型 (包装不改变连接池生命周期)."""
    inner = _FakeModel()
    model = RetryingChatModel(inner)

    await model.aclose()

    assert inner.closed is True


# ---------------------------------------------------------------------------
# 与 agent loop 的组合 (接线证据: 重试对 loop 透明)
# ---------------------------------------------------------------------------


async def test_loop_finishes_after_transient_failure(
    chat_model: HttpXChatModel,
) -> None:
    """loop + 重试包装: 429 后成功, 事件流仍以 final 收尾 (重试对 loop 透明)."""
    events: list[StreamEvent] = []
    model = RetryingChatModel(chat_model, policy=_policy())
    loop = AgentLoop(model=model, event_sink=events.append)

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(
            side_effect=[
                httpx.Response(429, json={"error": {"message": "rate limit exceeded"}}),
                httpx.Response(200, json=text_completion_json(reasoning=None)),
            ]
        )
        result = await loop.run(list(MESSAGES))

    assert result.outcome is LoopOutcome.FINISHED
    assert result.content == "订单已发货"
    assert result.turn_count == 1  # 重试不算 turn (loop 只看到一次成功的模型决策)
    assert len(route.calls) == 2
    assert [event.type for event in events] == [EventType.FINAL]


async def test_loop_propagates_when_retries_exhausted(
    chat_model: HttpXChatModel,
) -> None:
    """重试耗尽: 异常上抛且**不发终局事件** (框架层异常契约)."""
    events: list[StreamEvent] = []
    model = RetryingChatModel(chat_model, policy=_policy(max_attempts=2))
    loop = AgentLoop(model=model, event_sink=events.append)

    async with respx.mock() as router:
        route = router.post(CHAT_URL)
        route.mock(return_value=httpx.Response(500, text="internal error"))
        with pytest.raises(ModelStatusError) as exc_info:
            await loop.run(list(MESSAGES))

    assert exc_info.value.status_code == 500
    assert len(route.calls) == 2
    assert events == []  # 事件流中断即如实反映失败, 由 server 层按 §4 降级
