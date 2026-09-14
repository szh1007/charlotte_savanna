"""历史视图测试: 把一串快照渲染成人能看的表格 (issue 07 的回放调试面).

被测的是 checkpoint/utils/history.py 的三个纯函数:
- `summarize`: 一帧 -> 一行摘要
- `format_history`: 一串帧 -> 对齐的文本表格
- `display_width`: 终端显示宽度 (汉字占两格, 中文表格才不会越排越歪)

断言分两层: 「表格里有没有想看的字段」(内容) 与「每行是否等宽」(排版) —— 后者
用 display_width 度量, 于是中文列混排时也能发现对齐写歪了.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers import make_checkpoint, make_metadata, make_state

from CharAgent.checkpoint.utils.history import (
    display_width,
    format_history,
    short_id,
    summarize,
)
from CharAgent.checkpoint.utils.types import (
    CheckpointSource,
    Suspension,
)


def make_frames():
    """造一串「正常一轮 -> 分叉 -> 挂起」的帧, 覆盖表格里各列的特殊取值."""
    base = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
    origin = make_checkpoint(
        checkpoint_id="ck-0001-aaaa",
        thread_id="t-1",
        turn_number=1,
        created_at=base,
        metadata=make_metadata(turn_tokens=120, turn_elapsed_ms=880.0),
    )
    fork = make_checkpoint(
        checkpoint_id="ck-0002-bbbb",
        thread_id="t-1",
        turn_number=2,
        parent_id=origin.checkpoint_id,
        created_at=base + timedelta(minutes=1),
        metadata=make_metadata(
            source=CheckpointSource.FORK,
            turn_tokens=95,
            turn_elapsed_ms=610.5,
            tool_names=[],
            outcome=None,
            finish_reason=None,  # 还没结束: 两个结束字段都空着
            content=None,
        ),
    )
    suspended = make_checkpoint(
        checkpoint_id="ck-0003-cccc",
        thread_id="t-1",
        turn_number=3,
        parent_id=fork.checkpoint_id,
        created_at=base + timedelta(minutes=2),
        state=make_state(
            suspension=Suspension(reason="需要人工批准", pending=[], approval_id="a-1")
        ),
        metadata=make_metadata(
            source=CheckpointSource.SUSPENSION,
            turn_tokens=0,
            turn_elapsed_ms=12.0,
            tool_names=["refund_order"],
            finish_reason="tool_calls",
            outcome=None,
            content=None,
        ),
    )
    return [origin, fork, suspended]


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------


def test_summarize_covers_the_debugging_fields():
    """摘要含回放调试要看的字段: 编号 / 轮次 / 来源 / 本轮用量 / 工具 / 结束 / 挂起."""
    frame = make_frames()[1]

    summary = summarize(frame)

    assert summary["帧"] == "ck-0002-"  # 截短到 8 位
    assert summary["轮次"] == 2
    assert summary["来源"] == "fork"
    assert summary["本轮tok"] == 95
    assert summary["本轮ms"] == 610.5
    assert summary["工具"] == "-"  # 这一轮没调工具
    assert summary["结束"] == "-"  # 还没结束
    assert summary["挂起"] == "-"
    assert summary["父帧"] == "ck-0001-"


def test_summarize_marks_root_frame_and_suspension():
    """根帧写 (根), 挂起帧写挂起原因 (一眼看出哪帧卡住了)."""
    origin, _, suspended = make_frames()

    assert summarize(origin)["父帧"] == "(根)"
    assert summarize(suspended)["挂起"] == "需要人工批准"


def test_summarize_falls_back_to_finish_reason():
    """结束列优先写 run 结束原因, 没有就退而写最后一次响应的终止原因."""
    _, fork, suspended = make_frames()

    assert summarize(suspended)["结束"] == "tool_calls"  # 只有 finish_reason
    assert summarize(fork)["结束"] == "-"


def test_short_id_truncates():
    """编号截短工具: 只取前 8 位."""
    assert short_id("0123456789abcdef") == "01234567"


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------


def test_format_history_renders_one_line_per_frame():
    """每个帧一行, 加上表头与分隔线 (行数 = 帧数 + 2)."""
    frames = make_frames()

    lines = format_history(frames).splitlines()

    assert len(lines) == len(frames) + 2
    assert "帧" in lines[0] and "来源" in lines[0]
    assert set(lines[1]) == {"-"}  # 分隔线
    assert lines[2].startswith("ck-0001")
    assert "suspension" in lines[4]


def test_format_history_lines_are_aligned_with_chinese():
    """每行显示宽度一致: 中文列 (挂起原因) 也不能把表格排歪.

    这条是 display_width 存在的理由 —— 按字符个数补空格会在汉字上少补一格,
    表格立刻歪掉.
    """
    rendered = format_history(make_frames()).splitlines()

    widths = {display_width(line) for line in rendered}
    assert len(widths) == 1, f"各行宽度不一致: {sorted(widths)}"


def test_format_history_on_empty_list_explains_itself():
    """没有帧时给一句说明 (调试时「没有历史」和「打印失败」要分得清)."""
    assert "没有历史帧" in format_history([])


def test_display_width_counts_chinese_as_two_cells():
    """中文按两格算, 英文按一格算."""
    assert display_width("abc") == 3
    assert display_width("中文") == 4
    assert display_width("a中") == 3
