"""client 终端渲染测试: 六类事件怎么画成一行字.

场景 → 断言:
- 六类事件各有一行版式, 标签是 `[thinking]` 这类方括号词 (可 grep)
- tool_call 的 arguments 是原始 JSON 字符串: 能解析就压成紧凑一行, 畸形就
  原样打并标注 (畸形本身是要给用户看的信息, #2)
- tool_result 成功给「名字 ok (耗时): 摘要」, 失败给可操作错误
- reasoning 超长只打头一段并注明总长度 (终端没有「折叠」这个交互)
- final **只报怎么结束的**: 正文不在这一行里 (权威值在 LoopResult.content,
  两处都印会重复一遍答案)
- 颜色开关: 关掉时不出现任何 ANSI 转义符; 打开时包在转义符里
- 未知事件类型原样吐出而不是炸掉 (以后新增 approval_required 时)
- format_result / format_answer 的取值口径

被测对象是纯格式化 (无 IO): 事件对象就地构造, writer 只是收集列表.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from CharAgent.agent.utils.types import LoopOutcome, LoopResult
from CharAgent.client.render import (
    REASONING_LIMIT,
    EventPrinter,
    format_answer,
    format_arguments,
    format_outcome,
    format_result,
)
from CharAgent.model.utils.types import FinishReason
from CharAgent.stream.utils.types import EventType, StreamEvent

# ANSI 转义符的起始字节 (断言「没上色」时找它)
_ESCAPE = "\033"


def rendered(event_type: EventType, *, color: bool = False, **data: Any) -> str:
    """把一个事件喂给 printer, 返回它画出来的那一行 (不起模型也不跑 loop)."""
    lines: list[str] = []
    printer = EventPrinter(lines.append, color=color)
    printer(StreamEvent(type=event_type, seq=1, data=data))
    assert len(lines) == 1, "一个事件应当恰好画一行"
    return lines[0]


# ---------------------------------------------------------------------------
# 六类事件各一行
# ---------------------------------------------------------------------------


def test_thinking_line() -> None:
    """thinking: 非终止轮的助手正文 (过程叙述)."""
    assert rendered(EventType.THINKING, message="我先查一下订单", turn=1) == (
        "[thinking] 我先查一下订单"
    )


def test_tool_call_line_compacts_json_arguments() -> None:
    """tool_call: 工具名 + 压平后的参数 (原始 JSON 字符串解析一次只为好看)."""
    line = rendered(
        EventType.TOOL_CALL,
        tool_call_id="call_0",
        tool_name="query_order_status",
        arguments='{"order_no": "20260701123456"}',
        turn=1,
    )
    assert line == '[tool_call] query_order_status({"order_no": "20260701123456"})'


def test_tool_call_line_marks_malformed_json() -> None:
    """参数是畸形 JSON: 原样打并标注 —— 那正是模型填错的信号 (下一轮自纠错)."""
    line = rendered(
        EventType.TOOL_CALL,
        tool_call_id="call_0",
        tool_name="convert_length",
        arguments='{"value": 3.5',
        turn=1,
    )
    assert "畸形 JSON" in line
    assert '{"value": 3.5' in line


def test_tool_result_success_line_carries_summary_and_ms() -> None:
    """tool_result 成功: 名字 + ok + 耗时 + 摘要."""
    line = rendered(
        EventType.TOOL_RESULT,
        tool_call_id="call_0",
        tool_name="query_order_status",
        status="ok",
        duration_ms=12.0,
        summary="订单已发货",
        turn=1,
    )
    assert line == "[tool_result] query_order_status ok (12ms): 订单已发货"


def test_tool_result_sub_millisecond_keeps_one_decimal() -> None:
    """耗时不足 10ms 时保留一位小数 (全取整会显示成 0ms, 看不出快慢)."""
    line = rendered(
        EventType.TOOL_RESULT,
        tool_call_id="call_0",
        tool_name="echo",
        status="ok",
        duration_ms=0.25,
        summary="echo",
        turn=1,
    )
    assert "(0.2ms)" in line


def test_tool_result_error_line_carries_actionable_text() -> None:
    """tool_result 失败: 带上可操作错误原文 (#2), 不是一句「工具失败」."""
    line = rendered(
        EventType.TOOL_RESULT,
        tool_call_id="call_0",
        tool_name="query_order_status",
        status="error",
        duration_ms=1.0,
        error="order_no 应为 14 位数字, 实际 'ABC'",
        turn=1,
    )
    assert "[tool_result]" in line
    assert "失败" in line
    assert "应为 14 位数字" in line


def test_reasoning_line_truncates_and_reports_total() -> None:
    """reasoning 超长: 只打前 REASONING_LIMIT 字并注明总长度."""
    long_reasoning = "想" * (REASONING_LIMIT + 50)
    line = rendered(EventType.REASONING, delta=long_reasoning, turn=1)
    assert line.startswith("[reasoning] ")
    assert f"共 {REASONING_LIMIT + 50} 字" in line
    assert len(line) < len(long_reasoning)


def test_reasoning_line_keeps_short_text_as_is() -> None:
    """reasoning 不超长: 原样打, 不加任何省略说明."""
    assert rendered(EventType.REASONING, delta="核对订单号", turn=1) == (
        "[reasoning] 核对订单号"
    )


def test_final_line_reports_end_state_not_the_body() -> None:
    """final 只报「怎么结束的」; 正文由调用方从 LoopResult.content 打 (权威值)."""
    line = rendered(
        EventType.FINAL,
        content="订单 20260701123456 已发货",
        finish_reason="stop",
        outcome="finished",
        tokens=1234,
        elapsed_ms=4200.0,
    )
    assert "订单 20260701123456 已发货" not in line
    assert "finish_reason=stop" in line
    assert "1234 tokens" in line
    assert "4.2s" in line


def test_error_line_carries_code_and_message() -> None:
    """error: code + 事实性说明 (终局事件恰好一个)."""
    line = rendered(
        EventType.ERROR,
        error={"code": "max_turns", "message": "已达最大轮数限制, 未能产出最终答复"},
    )
    assert line == "[error] max_turns: 已达最大轮数限制, 未能产出最终答复"


def test_unknown_event_type_is_printed_raw_not_dropped() -> None:
    """未知事件类型 (如以后新增的 approval_required) 原样吐出, 不 KeyError 也不丢."""
    printer = EventPrinter(color=False)
    line = printer.format_event(
        StreamEvent(type=cast(EventType, "approval_required"), seq=1, data={"a": 1})
    )
    assert line.startswith("[approval_required]")
    assert "{'a': 1}" in line


# ---------------------------------------------------------------------------
# 颜色开关
# ---------------------------------------------------------------------------


def test_color_off_adds_no_escape_sequence() -> None:
    """关掉颜色: 一个转义符都不加 (重定向进文件才干净)."""
    line = rendered(
        EventType.TOOL_RESULT,
        color=False,
        tool_call_id="c",
        tool_name="echo",
        status="error",
        duration_ms=1.0,
        error="炸了",
        turn=1,
    )
    assert _ESCAPE not in line
    assert "[tool_result]" in line


def test_color_on_wraps_tag_and_error_text() -> None:
    """打开颜色: 标签与错误文本都包在转义符里 (终端上才有颜色)."""
    line = rendered(
        EventType.ERROR, color=True, error={"code": "cancelled", "message": "中断了"}
    )
    assert _ESCAPE in line
    assert line.rstrip().endswith("\033[0m")


# ---------------------------------------------------------------------------
# 取值口径
# ---------------------------------------------------------------------------


def test_format_arguments_passes_non_object_json_through() -> None:
    """参数是合法 JSON 但不是对象 (如数组): 原样返回, 不强行重排."""
    assert format_arguments("[1, 2]") == "[1, 2]"


def test_format_outcome_translates_every_enum_member() -> None:
    """每个 LoopOutcome 都有中文短语 (漏一个就会在终端上冒出英文枚举值)."""
    for outcome in LoopOutcome:
        text = format_outcome(outcome)
        assert text and text != outcome.value, f"{outcome} 缺中文短语"


def test_format_answer_states_absence_instead_of_printing_blank() -> None:
    """无正文时说一句实话 (否则用户以为程序卡住了)."""
    assert format_answer(None).startswith("(本次没有产出正文")
    assert format_answer("   ").startswith("(本次没有产出正文")
    assert format_answer("订单已发货") == "订单已发货"


def _make_result(**overrides: Any) -> LoopResult:
    """造一个 LoopResult 样本 (只填断言用得到的字段)."""
    fields: dict[str, Any] = {
        "messages": [],
        "content": "订单已发货",
        "finish_reason": FinishReason.STOP,
        "outcome": LoopOutcome.FINISHED,
        "turns": [],
        "turn_count": 2,
        "total_tokens": 1234,
        "elapsed_ms": 4200.0,
    }
    fields.update(overrides)
    return LoopResult(**fields)


def test_format_result_carries_turns_tokens_and_seconds() -> None:
    """结果摘要一行装下: 结束原因 / 轮数 / token / 秒数."""
    line = format_result(_make_result())
    assert line == "答完了 · 2 轮 · 1234 tokens · 4.2s"


@pytest.mark.parametrize(
    "outcome",
    [LoopOutcome.MAX_TURNS, LoopOutcome.TOKEN_BUDGET, LoopOutcome.TIME_LIMIT],
)
def test_format_result_translates_guard_outcomes(outcome: LoopOutcome) -> None:
    """guard 刹车也走同一行摘要 (只是结束原因换词), 不另写一套版式."""
    assert format_outcome(outcome) in format_result(_make_result(outcome=outcome))
