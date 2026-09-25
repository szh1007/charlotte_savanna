"""`trace` 只读入口的自检 (不需要数据库): 版式 / 措辞 / 参数.

这一片是**给人看的**那半边 —— 它的验收是「一眼看得出这次运行干了什么、花了多少」,
所以本文件把打印出来的每一行都钉住, 而不是只断言「里面有这几个数字」:

- 头两段 (账目 + 金额) 各自钉一条完整文本: 版式是最容易被顺手改坏的东西;
- **金额与算式都是从 run 行读的** (ticket 28 改判之后它们不再现算), 用例里那份
  明细是手写的 —— 它就是「库里某一行长这样」的样本, 顺带把落库的形状也钉住了;
- 「没有金额」的每种来路各钉一句措辞: 报 0 与报原因在屏幕上必须一眼能分开;
- 工具调用表逐列对齐 (含中文宽度的坑), 结果行挂在它那条调用下面.

真库那条路另有一份 (`test_client_trace_db.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from CharAgent.checkpoint.utils.history import display_width
from CharAgent.client.trace import (
    FrameView,
    TraceData,
    build_parser,
    parse_argv,
    render_trace,
)
from CharAgent.db import (
    Run,
    Thread,
    ToolCall,
    ToolCallStatus,
    build_tool_call,
)

START = datetime(2026, 9, 24, 10, 2, 11, tzinfo=UTC)
END = datetime(2026, 9, 24, 10, 2, 15, tzinfo=UTC)

ORDER_ARGS = '{"order_no": "20260701123456"}'

# 库里那一份**算得出来**的明细 (手写: 它就是落库形状的样本).
# 金额 0.001272 = 400 x 2/M + 0 x 0.5/M + 59 x 8/M —— 与上面那行用量对得上
COST_DETAIL: dict[str, Any] = {
    "kind": "cost",
    "tier": "valley",
    "total": "0.001272",
    "derived": [],
    "lines": [
        {"tier": "cache_miss", "tokens": 400, "price": "2", "amount": "0.0008"},
        {"tier": "cache_hit", "tokens": 0, "price": "0.5", "amount": "0"},
        {"tier": "output", "tokens": 59, "price": "8", "amount": "0.000472"},
    ],
}


def gap_detail(reason: str, **context: Any) -> dict[str, Any]:
    """库里那一份**算不出来**的明细."""
    return {"kind": "gap", "reason": reason, **context}


def make_run(**overrides: Any) -> Run:
    """造一行运行 (只给渲染用到的那些列, 其余留默认)."""
    values: dict[str, Any] = {
        "run_id": "run-1",
        "thread_id": "thread-1",
        "status": "finished",
        "model": "deepseek-flash",
        "prompt_version": "system/v1",
        "total_tokens": 459,
        "input_tokens": 400,
        "output_tokens": 59,
        "reasoning_tokens": 16,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 400,
        "total_cost": None,
        "total_cost_detail": None,
        "turn_count": 2,
        "created_at": START,
        "updated_at": END,
        "finished_at": END,
    }
    values.update(overrides)
    return Run(**values)


def make_thread(**overrides: Any) -> Thread:
    """造一行会话 (租户 / 属主从这里来)."""
    values: dict[str, Any] = {
        "thread_id": "thread-1",
        "tenant_id": "cli",
        "user_id": "cli",
    }
    values.update(overrides)
    return Thread(**values)


def make_call(
    *,
    tool_call_id: str = "call_0",
    tool_name: str = "get_my_order",
    arguments: str = ORDER_ARGS,
    status: ToolCallStatus = ToolCallStatus.SUCCEEDED,
    result: Any = None,
    duration_ms: int | None = 142,
) -> ToolCall:
    """造一条工具调用 (状态与结果由调用方指定)."""
    call = build_tool_call(
        run_id="run-1",
        message_id="run-1:1",
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments,
        status=status,
        created_at=START,
    )
    call.result = result
    call.duration_ms = duration_ms
    return call


# 「没给这个参数」与「明确给 None」是两回事 (会话行读不到那一条要能表达出来),
# 所以默认值用一个哨兵而不是 None
_UNSET = object()


def render(
    *,
    run: Run | None = None,
    thread: Any = _UNSET,
    calls: tuple[ToolCall, ...] = (),
    frames: tuple[FrameView, ...] = (),
    show_view: bool = False,
) -> str:
    """把一份轨迹渲染成文本 (默认给一行跑完、金额算好了的运行)."""
    return render_trace(
        TraceData(
            run=run
            if run is not None
            else make_run(
                total_cost=Decimal("0.001272"), total_cost_detail=COST_DETAIL
            ),
            thread=make_thread() if thread is _UNSET else thread,
            calls=calls,
            frames=frames,
        ),
        show_view=show_view,
    )


def line_of(text: str, prefix: str) -> str:
    """取以某个前缀开头的那一行 (断言措辞时只看这一行, 免得被别处的数字干扰)."""
    matched = [line for line in text.splitlines() if line.strip().startswith(prefix)]
    assert matched, f"没有以 {prefix!r} 开头的行:\n{text}"
    return matched[0]


def table_rows(text: str) -> list[str]:
    """工具调用表那几行 (表头 + 每次调用; 结果行不算, 末列补白也去掉)."""
    lines = text.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.startswith("工具调用")
    )
    rows = []
    for line in lines[start + 1 :]:
        if not line.startswith("  ") or line.startswith("      └"):
            break
        rows.append(line.rstrip())
    return rows


# ---------------------------------------------------------------------------
# 账目那一段
# ---------------------------------------------------------------------------


def test_head_prints_identity_status_and_window():
    """头四行: 编号 / 会话与归属 / 状态与版本 / 起止时刻与耗时.

    版式一行一条钉住: 这一段的读者是「排查的人」, 他习惯按位置找信息 —— 少一行
    或换一行位置都会让人以为「这条没记」.
    """
    text = render()

    assert text.splitlines()[:5] == [
        "run  run-1",
        "  会话 thread-1 (租户 cli / 用户 cli)",
        "  状态 finished · 模型 deepseek-flash · 提示词 system/v1",
        "  起止 2026-09-24 10:02:11Z → 2026-09-24 10:02:15Z (耗时 4.0s)",
        "  token input 400 (cache_hit 0 / cache_miss 400) · output 59"
        " (reasoning 16) · total 459",
    ]


def test_tokens_line_keeps_the_three_tiers_apart():
    """用量那行把三档拆开写 (cache_hit / cache_miss 分开), 而不是只报一个输入总量.

    拆开写是计价那条口径在屏幕上的兑现: 读者自己就能看出「这个 run 大部分输入
    都是命中的」—— 只报 464 的话, 那一层信息在展示的第一步就丢了.
    """
    text = render(
        run=make_run(input_tokens=464, cache_hit_tokens=256, cache_miss_tokens=208)
    )

    assert line_of(text, "token") == (
        "  token input 464 (cache_hit 256 / cache_miss 208) · output 59"
        " (reasoning 16) · total 459"
    )


def test_never_reported_components_print_as_dash_not_zero():
    """没上报的分量打 `-`, 不打 0 (NULL 与 0 是相反的结论)."""
    text = render(
        run=make_run(
            input_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
            reasoning_tokens=None,
            output_tokens=None,
        )
    )

    assert line_of(text, "token") == (
        "  token input - (cache_hit - / cache_miss -) · output - · total 459"
    )


def test_no_reasoning_is_not_printed_as_a_zero_parenthesis():
    """非推理模型 (上游不给 reasoning_tokens) 不打「(reasoning 0)」.

    「reasoning 0」看起来像「这个模型没思考」, 而真相是「这一栏没这个数据」——
    括号整块省掉.
    """
    text = render(run=make_run(reasoning_tokens=None))

    assert line_of(text, "token") == (
        "  token input 400 (cache_hit 0 / cache_miss 400) · output 59 · total 459"
    )


def test_unfinished_run_shows_no_end_time():
    """还在跑 (没有 finished_at) 的运行: 起止只写开始那一刻, 耗时打 `-`."""
    text = render(run=make_run(status="running", finished_at=None))

    assert line_of(text, "起止") == "  起止 2026-09-24 10:02:11Z → 未结束 (耗时 -)"


def test_missing_model_and_prompt_print_as_dash():
    """模型名 / 提示词版本为空时打 `-` (老数据与没装配身份说明的运行都是这样)."""
    text = render(run=make_run(model=None, prompt_version=None))

    assert line_of(text, "状态") == "  状态 finished · 模型 - · 提示词 -"


def test_missing_thread_row_still_renders():
    """会话行读不到时照样打得出来 (归属打 `-`), 不炸也不整段省掉."""
    text = render(thread=None)

    assert line_of(text, "会话") == "  会话 thread-1 (租户 - / 用户 -)"


# ---------------------------------------------------------------------------
# 金额那一段: 从库里读, 不重算
# ---------------------------------------------------------------------------


def test_amount_line_shows_the_total_and_its_three_tier_math():
    """金额行给合计, 下面一行把算式原样写出来 (算式也是库里存的那一份).

    只有合计的话, 「金额对不对」只能信框架 —— 而这一片本来就是「把事实折算成
    钱」, 算式是这条折算的说明书.
    """
    text = render()

    assert line_of(text, "金额") == "  金额 ¥0.001272 (valley)"
    assert (
        "    └ cache_miss 400 x ¥2/M + cache_hit 0 x ¥0.5/M + output 59 x ¥8/M" in text
    )


def test_the_amount_comes_from_the_row_not_a_recomputation():
    """金额取库里那一列, **不按用量重算** —— 这条是本片改判的正面证据.

    用例故意让「行里的金额」与「按用量算出来的金额」差得远远的: 只要打印的是行里
    那个数, 就说明入口没有自己再算一遍. 自己算的话会用**今天的**价目表, 而账单是
    按当时那一版开的.
    """
    text = render(
        run=make_run(total_cost=Decimal("9.99"), total_cost_detail=COST_DETAIL),
    )

    assert line_of(text, "金额") == "  金额 ¥9.99 (valley)"


def test_the_amount_carries_the_price_set_it_was_computed_with():
    """金额后面带档位 (peak / valley) —— 事后要看的就是「这笔按哪套价算的」."""
    peak = dict(COST_DETAIL, tier="peak")
    text = render(run=make_run(total_cost=Decimal("0.002544"), total_cost_detail=peak))

    assert line_of(text, "金额") == "  金额 ¥0.002544 (peak)"


def test_a_flat_priced_run_has_no_tier_marker():
    """全天一价的运行没有时段可标 (不打一个空括号)."""
    flat = dict(COST_DETAIL, tier=None)
    text = render(run=make_run(total_cost=Decimal("0.001272"), total_cost_detail=flat))

    assert line_of(text, "金额") == "  金额 ¥0.001272"


def test_a_derived_tier_is_marked_in_the_formula():
    """用输入总量减出来的那一档要标明来路.

    用量那行的同一个位置写着 `-` (那一列上游确实没报), 不标的话屏幕上会冒出一个
    「用量是 -, 算式里却有数」的档.
    """
    derived = dict(COST_DETAIL, derived=["cache_miss_tokens"])
    text = render(
        run=make_run(
            input_tokens=464,
            cache_miss_tokens=None,
            total_cost=Decimal("0.001272"),
            total_cost_detail=derived,
        )
    )

    assert "cache_miss 400 x ¥2/M (推自 input)" in text


def test_zero_amount_prints_a_real_zero():
    """真花了 0 元 -> `¥0` (与「没算出来」是两回事)."""
    zero = {
        "kind": "cost",
        "tier": "valley",
        "total": "0",
        "derived": [],
        "lines": [
            {"tier": "cache_miss", "tokens": 0, "price": "2", "amount": "0"},
            {"tier": "cache_hit", "tokens": 0, "price": "0.5", "amount": "0"},
            {"tier": "output", "tokens": 0, "price": "8", "amount": "0"},
        ],
    }
    text = render(
        run=make_run(
            total_tokens=0,
            input_tokens=0,
            cache_hit_tokens=0,
            cache_miss_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            total_cost=Decimal("0"),
            total_cost_detail=zero,
        )
    )

    assert line_of(text, "金额") == "  金额 ¥0 (valley)"


@pytest.mark.parametrize(
    ("detail", "expected", "model"),
    [
        (
            {"kind": "gap", "reason": "no_price", "model": "deepseek-flash"},
            "未配置单价 (模型 deepseek-flash 不在当时的价目表里)",
            "deepseek-flash",
        ),
        (
            {"kind": "gap", "reason": "no_price"},
            "未配置单价 (这次运行没记模型名, 查不了价)",
            None,
        ),
        (
            {"kind": "gap", "reason": "no_tokens", "missing": ["cache_miss_tokens"]},
            "算不出来 (上游没上报这几个分量: cache_miss_tokens)",
            "deepseek-flash",
        ),
        (
            {"kind": "gap", "reason": "no_calendar_data", "date": "2027-01-05"},
            "算不出来 (日历里没有 2027-01-05 所在的年份)",
            "deepseek-flash",
        ),
        (
            {"kind": "gap", "reason": "no_calendar_lib"},
            "算不出来 (当时没装中国节假日日历, 判不了峰谷)",
            "deepseek-flash",
        ),
        (
            {"kind": "gap", "reason": "no_moment"},
            "算不出来 (不知道这次运行从哪一刻开始, 判不了峰谷)",
            "deepseek-flash",
        ),
        ({"kind": "gap", "reason": "unfinished"}, "没跑完, 没有账目", "deepseek-flash"),
        (
            {"kind": "gap", "reason": "no_detail"},
            "库里没有这笔账 (这一趟还没收尾, 或它是改口径之前写的)",
            "deepseek-flash",
        ),
        (
            {"kind": "gap", "reason": "too_small"},
            "算出来的钱小于 0.000001 (这一列的最小刻度), 存不下",
            "deepseek-flash",
        ),
        (
            {"kind": "gap", "reason": "bad_config", "error": "少了 output 档"},
            "价目表没读成: 少了 output 档",
            "deepseek-flash",
        ),
    ],
)
def test_every_reason_has_its_own_sentence(
    detail: dict[str, Any], expected: str, model: str | None
):
    """库里那句「为什么没有金额」原样搬上屏幕.

    每种原因的**修法不同** (补配置 / 升依赖 / 查上游), 所以措辞在展示层一句一条,
    不合成「算不出来」一句 —— 那会把排查方向也吞了.
    """
    text = render(run=make_run(model=model, total_cost=None, total_cost_detail=detail))

    assert line_of(text, "金额") == f"  金额 (没有) {expected}"


def test_a_run_without_a_detail_column_says_it_is_unreadable():
    """老行: 有金额但没有明细 -> 金额照打, 算式那行如实说读不出来.

    金额是主 (它记的是那一笔账), 不能因为明细读不出来就把它藏起来.
    """
    text = render(run=make_run(total_cost=Decimal("0.001272"), total_cost_detail=None))

    assert line_of(text, "金额") == "  金额 ¥0.001272"
    assert "算式读不出来" in text


def test_a_broken_detail_does_not_hide_the_amount():
    """明细是坏数据 (读不懂) -> 同上, 金额照样打出来."""
    text = render(
        run=make_run(
            total_cost=Decimal("0.001272"), total_cost_detail={"kind": "什么鬼"}
        )
    )

    assert line_of(text, "金额") == "  金额 ¥0.001272"
    assert "算式读不出来" in text


def test_no_amount_at_all_is_not_printed_as_zero():
    """两列都空 (老行 / 没算) -> 打「没有金额」, **绝不打 ¥0**.

    「报 0 比报不出来更糟, 因为 0 看起来像个答案」—— 屏幕上出现 `¥0` 时, 读的人
    会得出「这次没花钱」这个错误结论.
    """
    text = render(run=make_run(total_cost=None, total_cost_detail=None))
    amount = line_of(text, "金额")

    assert "¥" not in amount
    assert "没有" in amount


# ---------------------------------------------------------------------------
# 工具调用那张表
# ---------------------------------------------------------------------------


def test_tool_call_table_lists_every_call_in_order():
    """每一次调用一行: 序号 / 工具名 / 状态 / 耗时 / 参数 (原样 JSON)."""
    text = render(
        calls=(
            make_call(),
            make_call(
                tool_call_id="call_1",
                tool_name="request_refund",
                status=ToolCallStatus.FAILED,
                duration_ms=31,
            ),
        )
    )

    assert table_rows(text) == [
        "  #  工具名          状态       耗时   参数",
        "  1  get_my_order    succeeded  142ms  " + ORDER_ARGS,
        "  2  request_refund  failed     31ms   " + ORDER_ARGS,
    ]


def test_tool_result_hangs_under_its_own_call():
    """结果行挂在**它自己那条调用**下面 (带 `└`), 别飘到别处."""
    text = render(
        calls=(
            make_call(result="订单已发货, 单号 SF123"),
            make_call(
                tool_call_id="call_1",
                tool_name="request_refund",
                result="这一单已经申请过退款",
            ),
        )
    )

    assert "      └ 结果: 订单已发货, 单号 SF123" in text
    lines = [line.rstrip() for line in text.splitlines()]
    rows = [index for index, line in enumerate(lines) if ORDER_ARGS in line]
    assert lines[rows[0] + 1].endswith("└ 结果: 订单已发货, 单号 SF123")
    assert lines[rows[1] + 1].endswith("└ 结果: 这一单已经申请过退款")


def test_a_call_without_a_result_gets_no_result_line():
    """还没跑出结果的调用 (pending / 挂起) 不留一行空的 `└ 结果:`."""
    text = render(calls=(make_call(status=ToolCallStatus.PENDING, result=None),))

    assert "└ 结果:" not in text


def test_result_that_is_not_text_is_rendered_as_json():
    """结构化结果 (JSONB 里的 dict / list) 打成一行 JSON, 别打出 Python 的引号."""
    text = render(calls=(make_call(result={"status": "已发货", "no": "SF123"}),))

    assert '└ 结果: {"status": "已发货", "no": "SF123"}' in text


def test_call_without_duration_prints_a_dash():
    """没跑到计时那一步的调用, 耗时打 `-` (不是 0ms —— 0 是在说「瞬间就完成了」)."""
    text = render(calls=(make_call(duration_ms=None),))

    assert " ".join(table_rows(text)[1].split()).startswith(
        "1 get_my_order succeeded -"
    )


def test_long_arguments_are_truncated_with_a_count():
    """超长参数截断并报出总长度 (截断处要说清「这不是全部」)."""
    long_args = '{"order_no": "20260701123456", "note": "' + "很长的备注" * 20 + '"}'
    text = render(calls=(make_call(arguments=long_args),))
    row = table_rows(text)[1]

    assert "..." in row
    assert f"共 {len(long_args)} 字" in row
    assert long_args not in row


def test_arguments_are_printed_verbatim_even_when_malformed():
    """参数原样打出来, **哪怕它是畸形 JSON** (不预解析, 更不修它).

    `charagent_tool_calls.arguments` 那一列的注释就是这么定的: 畸形 JSON 正是自纠错
    路径的信号 (#2), 解析了反而丢证据. 所以这里刻意不走 `render.format_arguments`.
    """
    text = render(calls=(make_call(arguments='{"order_no": '),))

    assert '{"order_no":' in text


def test_cjk_tool_name_keeps_the_table_aligned():
    """工具名里有中文时表格仍然对齐 (汉字占两格, 按字符数补空格会歪)."""
    text = render(
        calls=(
            make_call(tool_name="查订单"),
            make_call(tool_call_id="call_1", tool_name="get_my_order_long_name"),
        )
    )

    rows = [row for row in table_rows(text) if ORDER_ARGS in row]
    starts = {display_width(row[: row.index(ORDER_ARGS)]) for row in rows}
    assert len(starts) == 1, f"参数列没对齐: {rows}"


def test_no_tool_calls_says_it_plainly():
    """一次没调工具 (纯问答) 时明说一句, 而不是留一张只有表头的空表."""
    text = render()

    assert "工具调用 (0 次)" in text
    assert "这次运行没有调用工具" in text


# ---------------------------------------------------------------------------
# 第二档: --view 展开帧里的视图
# ---------------------------------------------------------------------------


def frame(turn: int, **view: Any) -> FrameView:
    """造一帧的视图观察值 (只带要断言的那几个键; 一个键都不带给 None)."""
    return FrameView(turn_number=turn, created_at=START, view=view or None)


def test_view_tier_prints_each_frames_view():
    """`--view` 把每帧那轮**真的发出去**的东西摊开: 估算 / 漂移 / 命中率 / 压缩账."""
    text = render(
        frames=(
            frame(0, estimated_tokens=410, estimate_drift=-49, cache_hit_ratio=0.0),
            frame(
                1,
                estimated_tokens=520,
                estimate_drift=23,
                cache_hit_ratio=0.34,
                dropped=2,
                truncated=1,
                reasoning_cleared=0,
                saved_tokens=88,
                summarized=True,
                messages=[{"role": "user"}, {"role": "tool"}],
            ),
        ),
        show_view=True,
    )

    assert "帧视图 (2 帧;" in text
    assert "  轮 0  估算 410 tok (漂移 -49) · 命中率 0.00" in text
    assert (
        "  轮 1  估算 520 tok (漂移 +23) · 命中率 0.34 · 裁掉 2 条 / 截短 1 条 / "
        "清思维链 0 段 (省 88) · 已更新摘要 · 视图 2 条" in text
    )


def test_view_tier_is_off_by_default():
    """不给 `--view` 就不打这一档 (默认输出只管「干了什么、花了多少」)."""
    text = render(frames=(frame(0, estimated_tokens=410),))

    assert "帧视图" not in text


def test_view_tier_without_frames_says_why():
    """`--view` 给了但一帧都没有 -> 说清可能是没配快照存储, 而不是留一片空白."""
    text = render(show_view=True)

    assert "帧视图 (0 帧)" in text
    assert "没配快照" in text


def test_a_frame_without_a_view_names_both_possible_reasons():
    """视图为空时把**两种来路**都说出来, 不替读的人挑一种.

    2026-09-24 真机跑出来的就是这一条: 框架 CLI 不配压缩策略, 于是每一帧都没有
    视图 —— 而屏幕上却写着「这一轮没有模型调用」, 把「没配策略」说成了「没调模型」.
    """
    text = render(frames=(frame(0),), show_view=True)

    assert "  轮 0  没落视图" in text
    assert "没配压缩策略" in text and "没有模型调用" in text


def test_a_frame_with_only_some_keys_still_renders():
    """老帧的视图少几个键也打得出来 (只写库里真有的那些)."""
    text = render(frames=(frame(0, estimated_tokens=410),), show_view=True)

    assert "  轮 0  估算 410 tok" in text
    assert "漂移" not in text and "命中率" not in text


# ---------------------------------------------------------------------------
# 命令行参数
# ---------------------------------------------------------------------------


def test_parser_takes_a_run_id_and_an_optional_view_flag():
    """用法: `trace <run_id> [--view]` (与 app.py 同一套 argparse 组织)."""
    assert parse_argv(["abc123"]).run_id == "abc123"
    assert parse_argv(["abc123"]).show_view is False
    assert parse_argv(["--view", "abc123"]).show_view is True


def test_run_id_is_required():
    """不给编号是用法错 (argparse 自己报错并给退出码 2)."""
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
