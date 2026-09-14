"""轨迹断言工具的自测 (issue 09 / #62).

断言帮手自己也要有防线: 一个「永远通过」的轨迹断言比没有更危险 —— 它会让
一批轨迹用例假绿. 所以本文件把每条断言的正路与**反路**都走一遍 (不符时必须
红, 且报错信息要能读出「期望什么、实际什么、哪一项不符」).

另附确定性 (#61): 采样参数没钉死时, 确定性断言必须能发现.
"""

from __future__ import annotations

from typing import Any

import pytest
from mock_llm import (
    MockLLM,
    make_tool_call,
    request_record,
    text_response,
    tool_call_response,
)
from trace_assertions import (
    PINNED_SEED,
    PINNED_TEMPERATURE,
    Trace,
    parse_arguments,
    pinned_sampling,
    trace_of,
)

from CharAgent.agent import AgentLoop
from CharAgent.tool import tool

USER_MSG = {"role": "user", "content": "订单 20260701123456 到哪了"}
TOOL_SPEC: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "query_order", "parameters": {}}}
]

ORDER_CALL = tool_call_response(
    make_tool_call("query_order", '{"order_no": "20260701123456"}')
)
REFUND_CALL = tool_call_response(
    make_tool_call("refund", '{"order_no": "20260701123456"}')
)
FINAL = text_response("已发货")


def request_of(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = TOOL_SPEC,
    temperature: float | None = PINNED_TEMPERATURE,
    seed: int | None = PINNED_SEED,
) -> dict[str, Any]:
    """手工造一条请求记录 (构造退化轨迹用, 比如「工具结果没回填」)."""
    return request_record(
        messages,
        tools,
        temperature=temperature,
        top_p=None,
        seed=seed,
        max_tokens=None,
        thinking=None,
        reasoning_effort=None,
        stream=False,
    )


def two_turn_trace() -> Trace:
    """一条正常的两轮轨迹: 第 1 轮调工具, 第 2 轮出答案."""
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_query_order",
                "type": "function",
                "function": {
                    "name": "query_order",
                    "arguments": '{"order_no": "20260701123456"}',
                },
            }
        ],
    }
    tool_msg = {
        "role": "tool",
        "tool_call_id": "call_query_order",
        "content": "订单 20260701123456 已发货",
    }
    return Trace(
        calls=[request_of([USER_MSG]), request_of([USER_MSG, assistant, tool_msg])],
        responses=[ORDER_CALL, FINAL],
    )


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------


async def test_trace_extracts_calls_in_order_with_parsed_args() -> None:
    """轨迹按顺序摊平工具调用, 参数解析成 dict, turn 从 1 数."""
    trace = await run_loop([ORDER_CALL, REFUND_CALL, FINAL])

    assert trace.turn_count == 3
    assert trace.tool_names == ["query_order", "refund"]
    assert [record.turn for record in trace.tool_calls] == [1, 2]
    assert trace.tool_calls[0].args == {"order_no": "20260701123456"}
    assert trace.tool_calls[0].arguments == '{"order_no": "20260701123456"}'
    assert trace.contents == [None, None, "已发货"]


def test_seen_returns_frozen_history_per_turn() -> None:
    """`seen(turn)` 给出该轮模型看到的历史 (第 1 轮 1 条, 第 2 轮 3 条)."""
    trace = two_turn_trace()

    assert len(trace.seen(1)) == 1
    assert len(trace.seen(2)) == 3
    with pytest.raises(AssertionError, match="第 3 轮不存在"):
        trace.seen(3)


def test_trace_of_slices_multiple_runs() -> None:
    """同一实例跑多次 run 时按段取轨迹 (每段的 turn 从 1 重数)."""
    calls = [request_of([USER_MSG]) for _ in range(3)]

    class _Recorder:
        def __init__(self) -> None:
            self.calls = calls
            self.responses = [FINAL, ORDER_CALL, FINAL]

    whole = trace_of(_Recorder())
    second = trace_of(_Recorder(), start=1, end=2)

    assert whole.turn_count == 3
    assert second.turn_count == 1
    assert second.tool_names == ["query_order"]


def test_trace_of_empty_reports_model_never_called() -> None:
    """轨迹为空时直说「模型一次都没被调用」(而不是让断言莫名其妙地过)."""

    class _Recorder:
        calls: list[dict[str, Any]] = []
        responses: list[Any] = []

    with pytest.raises(AssertionError, match="一次都没被调用"):
        trace_of(_Recorder())


def test_parse_arguments_tolerates_malformed_json() -> None:
    """畸形 JSON / 非对象都返回 None (参数断言的失败信息里会说明)."""
    assert parse_arguments('{"a": 1}') == {"a": 1}
    assert parse_arguments("{不是 JSON") is None
    assert parse_arguments("[1, 2]") is None


# ---------------------------------------------------------------------------
# 断言: 正路
# ---------------------------------------------------------------------------


def test_assert_tool_calls_accepts_names_and_argument_subset() -> None:
    """期望项: 字符串断名字; 二元组断参数子集 (多余键不较真)."""
    trace = two_turn_trace()

    trace.assert_tool_calls([("query_order", {"order_no": "20260701123456"})])
    trace.assert_tool_names(["query_order"])
    trace.assert_turn_count(2)
    trace.assert_tool_result_backfilled("query_order", contains="已发货")


def test_assert_tool_calls_exact_false_allows_trailing_calls() -> None:
    """exact=False: 期望是实际序列的前缀即可 (后面还调了别的工具不算错)."""

    class _Recorder:
        def __init__(self) -> None:
            self.calls = [request_of([USER_MSG])] * 2
            self.responses = [ORDER_CALL, REFUND_CALL]

    trace = trace_of(_Recorder())

    trace.assert_tool_names(["query_order"], exact=False)
    with pytest.raises(AssertionError):
        trace.assert_tool_names(["query_order"])


# ---------------------------------------------------------------------------
# 断言: 反路 (不符必须红, 且信息可读)
# ---------------------------------------------------------------------------


def test_assert_tool_calls_reports_order_mismatch() -> None:
    """顺序不符: 报错里同时给出期望序列、实际序列与第一条不符的原因."""
    trace = two_turn_trace()

    with pytest.raises(AssertionError) as excinfo:
        trace.assert_tool_calls([("refund", {"order_no": "20260701123456"})])

    message = str(excinfo.value)
    assert "期望工具名 'refund'" in message
    assert "实际 'query_order'" in message
    assert "第 1 项不符" in message


def test_assert_tool_calls_reports_argument_mismatch() -> None:
    """参数不符: 说清是哪个键、期望值、实际值."""
    trace = two_turn_trace()

    with pytest.raises(AssertionError) as excinfo:
        trace.assert_tool_calls([("query_order", {"order_no": "9999"})])

    message = str(excinfo.value)
    assert "参数 'order_no' 不符" in message
    assert "'9999'" in message


def test_assert_tool_calls_reports_count_mismatch() -> None:
    """条数不符: 报错里两个序列都列出来 (一眼看出多调了还是少调了)."""
    trace = two_turn_trace()

    with pytest.raises(AssertionError) as excinfo:
        trace.assert_tool_calls(["query_order", "refund"])

    message = str(excinfo.value)
    assert "工具调用条数" in message
    assert "期望 (2)" in message
    assert "实际 (1)" in message


def test_assert_tool_calls_flags_malformed_arguments() -> None:
    """参数是畸形 JSON 时, 参数断言直说「无法按参数断言」并给出原文."""
    trace = Trace(
        calls=[request_of([USER_MSG])],
        responses=[tool_call_response(make_tool_call("query_order", "{坏 JSON"))],
    )

    with pytest.raises(AssertionError, match="不是合法 JSON"):
        trace.assert_tool_calls([("query_order", {"order_no": "x"})])


def test_assert_no_tool_calls_fails_when_called() -> None:
    """纯文本路径用例: 调了工具就是不符合预期."""
    trace = two_turn_trace()

    with pytest.raises(AssertionError, match="期望不调工具"):
        trace.assert_no_tool_calls()


def test_assert_turn_count_reports_actual_rounds() -> None:
    """轮数不符: 报错里带上分轮轨迹 (哪几轮多跑了看得见)."""
    trace = two_turn_trace()

    with pytest.raises(AssertionError) as excinfo:
        trace.assert_turn_count(3)

    assert "期望 3 轮, 实际 2 轮" in str(excinfo.value)


def test_assert_tool_result_backfilled_detects_missing_backfill() -> None:
    """工具结果没回填到下一轮 → 红 (这是「模型看不到结果」的回归)."""
    trace = Trace(
        calls=[request_of([USER_MSG]), request_of([USER_MSG])],  # 第二轮没有 tool 消息
        responses=[ORDER_CALL, FINAL],
    )

    with pytest.raises(AssertionError, match="没有回填"):
        trace.assert_tool_result_backfilled("query_order")


def test_assert_tool_result_backfilled_flags_call_in_final_turn() -> None:
    """最后一轮才发出的调用没有任何后续请求携带结果 → 直说这一点."""
    trace = Trace(
        calls=[request_of([USER_MSG])],
        responses=[ORDER_CALL],
    )

    with pytest.raises(AssertionError, match="没有任何后续请求"):
        trace.assert_tool_result_backfilled("query_order")


def test_assert_tool_result_backfilled_flags_unknown_tool() -> None:
    """轨迹里没有这个工具 → 报错列出实际调过什么."""
    trace = two_turn_trace()

    with pytest.raises(AssertionError, match="实际调过"):
        trace.assert_tool_result_backfilled("refund")


# ---------------------------------------------------------------------------
# 确定性断言 (#61)
# ---------------------------------------------------------------------------


def test_pinned_sampling_carries_temperature_and_seed() -> None:
    """确定性采样参数就这两个 (思考模式下的限定见 trace.py 注释)."""
    assert pinned_sampling() == {"temperature": 0.0, "seed": PINNED_SEED}


async def test_assert_pinned_sampling_accepts_pinned_loop() -> None:
    """按 pinned_sampling() 构造的 loop: 每轮参数都对得上."""
    trace = await run_loop([ORDER_CALL, FINAL], **pinned_sampling())

    trace.assert_pinned_sampling()


async def test_assert_pinned_sampling_detects_missing_seed() -> None:
    """漏传 seed → 红 (漏了它「同输入同输出」就不成立, 快照会变偶发)."""
    trace = await run_loop([ORDER_CALL, FINAL], temperature=PINNED_TEMPERATURE)

    with pytest.raises(AssertionError) as excinfo:
        trace.assert_pinned_sampling()

    assert "采样参数没有钉死" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 载体
# ---------------------------------------------------------------------------


def echo_tool() -> Any:
    """本地回显工具 (只为让 loop 有工具可调)."""

    def _echo(message: str) -> str:
        """回显载体: 返回收到的消息."""
        return f"echo:{message}"

    return tool(_echo, name="echo")


async def run_loop(script: list[Any], **sampling: Any) -> Trace:
    """跑一轮带工具的 loop, 返回轨迹 (轨迹工具的真实取数路径)."""
    model = MockLLM.scripted(script)
    loop = AgentLoop(model=model, tools=[echo_tool()], **sampling)
    await loop.run([USER_MSG])
    return trace_of(model)
