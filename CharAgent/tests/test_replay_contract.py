"""真实样本回放 + 三方契约 (#63).

三条解析路径对**同一份真实 wire 响应**必须给出等价的 ModelResponse:

| 路径 | 走法 |
|------|------|
| 回放 | `MockLLM.replay()` → model/parse.py 的 `parse_chat_completion` |
| httpx 裸调 | respx 拦截 → HttpXChatModel → 同一个 `parse_chat_completion` |
| openai SDK | respx 拦截 → OpenAIChatModel → `model_dump` 回 wire 结构后同一套纯函数 |

样本是真实 DeepSeek 录下来的 (tests/fixtures/llm/, 见 record_llm_samples.py):
于是「解析层面对真实响应读得对不对」在**零网络**的前提下被回归 —— 这正是
「RUN_INTEGRATION 样本来源 (#61 录制回放)」真正落地的地方.

契约判定沿用 `test_model_contract.assert_same_response` (语义字段全等, raw 允许
规范化差异), 与合成样本那组用例同一把尺子.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from helpers import API_KEY, BASE_URL, CHAT_URL
from mock_llm import MockLLM, load_sample
from record_llm_samples import TOOLS, USER_QUESTION, make_loop

# 复用契约测试的判等函数 (同一把尺子): 它就是「两适配器等价」的定义处, 复制一份
# 到这里等于把尺子劈成两半, 改一边不改另一边就假绿了
from test_model_contract import assert_same_response
from trace_assertions import trace_of

from CharAgent.model import HttpXChatModel, OpenAIChatModel

SAMPLES = ("text_stop", "tool_path", "tool_path_thinking")
ADAPTERS = (HttpXChatModel, OpenAIChatModel)


async def _fetch_with_recorded_response(
    adapter: HttpXChatModel | OpenAIChatModel,
    sample_name: str,
    index: int,
) -> Any:
    """把样本第 index 轮的**响应原文**喂给适配器, 返回它的 ModelResponse.

    同时把样本里那一轮的请求原样发出去 (消息 / 工具都对得上), 于是「喂进去的
    是什么」与「真实那一轮是什么」一致, 不靠人为拼接.
    """
    sample = load_sample(sample_name)
    request = sample.requests[index]
    async with respx.mock() as router:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json=sample.exchanges[index].response)
        )
        return await adapter.generate(
            request["messages"],
            request["tools"],
            temperature=request["temperature"],
            thinking=request["thinking"],
        )


@pytest.mark.parametrize("name", SAMPLES)
@pytest.mark.parametrize("adapter_cls", ADAPTERS, ids=["httpx", "sdk"])
async def test_adapter_reproduces_recorded_response(
    adapter_cls: type[HttpXChatModel] | type[OpenAIChatModel],
    name: str,
) -> None:
    """三方契约: 两个适配器对真实样本的每一轮都给出与回放等价的响应."""
    sample = load_sample(name)
    adapter = adapter_cls(api_key=API_KEY, base_url=BASE_URL, model="deepseek-flash")
    try:
        for index, expected in enumerate(sample.responses):
            actual = await _fetch_with_recorded_response(adapter, name, index)
            assert_same_response(actual, expected)
    finally:
        await adapter.aclose()


def test_recorded_thinking_sample_keeps_reasoning_separate() -> None:
    """真实思考样本的 reasoning_content 与正文分属两条通道 (#11).

    同时钉住**回填契约**: 下一轮请求里的 assistant 消息既带 reasoning_content
    (官方要求带 tools 时回传, 否则真实端点 400), 正文仍是原来那段文字.
    """
    sample = load_sample("tool_path_thinking")
    tool_turn = sample.responses[0]

    assert tool_turn.reasoning, "样本工具轮应当带思维链"
    # 思考模式的工具轮只吐思维链 + 工具调用, 正文常是空串 (实测如此) ——
    # 断言「两条通道没串线」而不是「正文一定有内容」
    assert tool_turn.content != tool_turn.reasoning

    assistant = _assistant_message(sample.requests[1]["messages"])
    assert assistant["reasoning_content"] == tool_turn.reasoning
    assert assistant["content"] == tool_turn.content


async def test_loop_backfills_reasoning_exactly_as_recorded() -> None:
    """loop 自己跑一遍思考样本: 回填的 wire 与录制时逐字一致 (#11 + #63).

    开着 `verify_requests` —— 比的是**整条消息历史** (含 reasoning_content 与
    tool 消息配对), 所以这不只是「有回填」, 而是「回填得和真实链路一模一样」.
    """
    model = MockLLM.replay("tool_path_thinking", verify_requests=True)
    loop = make_loop(model, tools=TOOLS, thinking=True)

    result = await loop.run([{"role": "user", "content": USER_QUESTION}])

    trace = trace_of(model)
    trace.assert_turn_count(2)
    trace.assert_tool_calls([("query_order", {"order_no": "20260701123456"})])
    assert result.content
    # 第 2 轮看到的历史里, 工具轮的思维链已经回填 (与录制样本一致才会通过校验)
    assert _assistant_message(trace.seen(2))["reasoning_content"]


def _assistant_message(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """取消息历史里第一条 assistant 消息 (工具轮)."""
    for message in messages:
        if message.get("role") == "assistant":
            return message
    raise AssertionError(f"消息历史里没有 assistant 消息: {messages}")
