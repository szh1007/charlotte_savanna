"""tool 执行包装测试 (#2): 校验错误与异常的可操作语义.

- 成功路径: sync/async 函数、dict 返回 JSON 文本化、None 返回空串
- 参数校验失败: 缺必填 / 类型错 / pattern / 枚举越界 / 数值越界 / 未知参数 /
  嵌套字段路径, 均给「字段 + 期望 + 实际」的可操作文本, 不甩原始 traceback
- 作者 raise ToolActionableError: 原文透传
- 意外异常: 通用内部错误文案 (不外泄 traceback), 根因保留 exception
- 畸形 JSON / 非对象 arguments 的可操作提示
- **工具级超时 (#15)**: 协程卡住 → 到点中止 + 可操作文本; 同步卡住 → 只放弃
  等待 (线程照跑, 日志如实记一条); 缺省值取全局配置; 取消 (kill switch) 不被
  超时那道闸吞掉

载体工具就地定义 (Seam 3); 类型需模块顶层可见.
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import StrEnum
from typing import Annotated, Literal

import pytest
from doubles import hang_forever
from pydantic import BaseModel, Field

import CharAgent.tool.executor as executor_module
from CharAgent.tool import (
    Tool,
    ToolActionableError,
    ToolConfigError,
    ToolTimeoutError,
    execute_tool,
    tool,
)

# ---------------------------------------------------------------------------
# 载体工具 (模块顶层, sync / async / 抛错 / 单 BaseModel 形态)
# ---------------------------------------------------------------------------


class Inner(BaseModel):
    """嵌套输入载体."""

    value: Annotated[float, Field(description="数值, 大于 0", gt=0)]
    unit: Literal["meter", "foot"] = Field(description="单位")


def _echo_sync(
    message: Annotated[str, Field(description="消息", min_length=1)],
    count: Annotated[int, Field(description="重复次数", ge=1, le=5)] = 2,
) -> str:
    """同步 echo 载体."""

    return (message + " ") * count


async def _echo_async(
    message: Annotated[str, Field(description="消息")],
) -> str:
    """异步 echo 载体 (验证 iscoroutinefunction 分支)."""

    return f"async:{message}"


def _records(
    items: Annotated[list[Inner], Field(description="输入列表")],
) -> str:
    """嵌套校验 + dict 返回载体."""

    return {"rows": [item.model_dump() for item in items]}


def _strict_pattern(
    code: Annotated[
        str,
        Field(description="14 位数字", pattern=r"^\d{14}$"),
    ],
) -> str:
    """pattern 约束载体."""

    return f"code={code}"


def _business_rule(
    order_no: Annotated[str, Field(description="订单号")],
) -> str:
    """业务规则载体: 跨 schema 校验在函数内 raise 可操作错误 (#2)."""

    if not order_no.isdigit():
        raise ToolActionableError(
            f"order_no 应为 14 位数字, 实际 {order_no!r}, 请核对后重试"
        )
    return "查询成功"


def _boom(
    dividend: Annotated[float, Field(description="被除数")],
    divisor: Annotated[float, Field(description="除数")],
) -> str:
    """意外异常载体 (ZeroDivisionError 应被包装为内部错误)."""

    return str(dividend / divisor)


def _returns_none(
    flag: Annotated[bool, Field(description="标志")] = True,
) -> str:
    """None 返回载体 (回填文本应为空串)."""


def _manual_echo(
    order_no: str,
    fuzzy: bool = False,
) -> str:
    """manual 引擎形状载体 (无 pydantic 校验模型).

    Args:
        order_no: 订单号.
        fuzzy: 是否模糊匹配.
    """

    return f"ok:{order_no}"


class _Filter(BaseModel):
    """单 BaseModel 参数形态载体."""

    kind: str = Field(description="类别")
    days: int = Field(description="天数", ge=1, default=3)


def _single_model_fn(params: _Filter) -> str:
    """唯一 BaseModel 参数形态: 收到模型实例."""

    return f"{params.kind} {params.days} 天"


class _Palette(StrEnum):
    """枚举越界载体 (StrEnum docstring 会污染 schema 描述, 不写)."""

    RED = "red"
    BLUE = "blue"


def _pick_color(
    color: Annotated[_Palette, Field(description="颜色")] = _Palette.RED,
) -> str:
    """挑颜色载体."""

    return f"picked {color.value}"


async def _raise_its_own_timeout() -> str:
    """工具自己抛 TimeoutError 的载体 (如 socket / 连接池超时, 不是框架掐的)."""

    raise TimeoutError("socket timed out")


async def _raise_the_timeout_type() -> str:
    """工具自己抛 ToolTimeoutError 的载体 (按同一语义声明「结果未知」)."""

    raise ToolTimeoutError("下游连接读超时, 结果未知")


# 模块级注册 (供用例复用)
ECHO = tool(_echo_sync)
ECHO_ASYNC = tool(_echo_async)
RECORDS = tool(_records)
STRICT = tool(_strict_pattern)
BUSINESS = tool(_business_rule)
BOOM = tool(_boom)
NONE_TOOL = tool(_returns_none)
SINGLE = tool(_single_model_fn)
PICK_COLOR = tool(_pick_color)
MANUAL_ECHO = tool(_manual_echo, schema="manual")
# 超时载体: 0.05 秒够短 (用例不必真等), 又远大于「函数调用本身」的开销
HANG_ASYNC = tool(hang_forever, name="hang_async", timeout=0.05)
HANG_ASYNC_BY_DEFAULT = tool(hang_forever, name="hang_async_by_default")
SELF_TIMEOUT = tool(_raise_its_own_timeout, name="self_timeout")
DECLARED_TIMEOUT = tool(_raise_the_timeout_type, name="declared_timeout")


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------


async def test_execute_sync_success():
    """sync 工具经线程池执行成功, content 为返回文本 (含默认参数)."""
    execution = await execute_tool(ECHO, arguments='{"message": "hi", "count": 3}')
    assert execution.ok and execution.content == "hi hi hi "
    execution = await execute_tool(ECHO, arguments='{"message": "hi"}')
    assert execution.ok and execution.content == "hi hi "  # count 默认 2


async def test_execute_async_success():
    """async 工具直接 await 执行成功."""
    execution = await execute_tool(ECHO_ASYNC, arguments='{"message": "你好"}')
    assert execution.ok and execution.content == "async:你好"


async def test_dict_result_serialized_to_json():
    """dict 返回自动 JSON 文本化 (ensure_ascii=False, 中文不转义)."""
    execution = await execute_tool(
        RECORDS,
        arguments=(
            '{"items": [{"value": 2.5, "unit": "meter"}, '
            '{"value": 100, "unit": "foot"}]}'
        ),
    )
    assert execution.ok
    assert '"rows"' in execution.content
    assert "meter" in execution.content


async def test_none_result_empty_content():
    """无 return (None) → content 为空串, ok=True."""
    execution = await execute_tool(NONE_TOOL, arguments="{}")
    assert execution.ok and execution.content == ""


async def test_single_base_model_param_execution():
    """唯一 BaseModel 形态: 校验后以模型实例调用函数."""
    execution = await execute_tool(SINGLE, arguments='{"kind": "加急"}')
    assert execution.ok and execution.content == "加急 3 天"
    execution = await execute_tool(SINGLE, arguments='{"kind": "加急", "days": 7}')
    assert execution.ok and execution.content == "加急 7 天"


async def test_duration_ms_recorded():
    """duration_ms 记录执行耗时 (>= 0)."""
    execution = await execute_tool(ECHO, arguments='{"message": "x"}')
    assert execution.ok and execution.duration_ms >= 0


# ---------------------------------------------------------------------------
# 参数校验失败 → 可操作错误 (#2, 验收项 4)
# ---------------------------------------------------------------------------


async def test_malformed_json_actionable():
    """畸形 JSON: 提示期望 JSON 对象, 而非抛异常."""
    execution = await execute_tool(ECHO, arguments="not json {")
    assert not execution.ok
    assert "arguments 不是合法 JSON" in execution.error
    assert execution.exception is None


async def test_non_object_arguments_actionable():
    """顶层非对象 (数组/标量): 指明期望对象形态."""
    execution = await execute_tool(ECHO, arguments='["hi"]')
    assert not execution.ok
    assert "应为 JSON 对象" in execution.error


async def test_missing_required_field_names_param():
    """缺必填参数: 明确指出字段名."""
    execution = await execute_tool(ECHO, arguments="{}")
    assert not execution.ok
    assert "缺少必填参数 message" in execution.error
    assert "Traceback" not in execution.error


async def test_wrong_type_reports_expected_and_actual():
    """类型错误: 说清期望类型与实际值 (不甩 422)."""
    execution = await execute_tool(ECHO, arguments='{"message": 123}')
    assert not execution.ok
    assert "参数 message" in execution.error
    assert "期望 字符串" in execution.error
    assert "实际: 123" in execution.error


async def test_pattern_mismatch_reports_pattern_and_actual():
    """pattern 违例: 给出格式期望与实际值 (正则原文, 不带转义)."""
    execution = await execute_tool(STRICT, arguments='{"code": "abc"}')
    assert not execution.ok
    assert "参数 code" in execution.error
    assert "格式不符合要求" in execution.error
    assert r"^\d{14}$" in execution.error
    assert "实际: 'abc'" in execution.error
    assert "\\\\d" not in execution.error  # 正则不经 repr 转义


async def test_enum_literal_out_of_range_lists_allowed():
    """枚举越界: 给出允许值列表 (literal_error 映射)."""
    execution = await execute_tool(PICK_COLOR, arguments='{"color": "pink"}')
    assert not execution.ok
    assert "取值必须在允许列表中" in execution.error
    assert "red" in execution.error and "blue" in execution.error
    assert "实际: 'pink'" in execution.error


async def test_numeric_bounds_report_min_max():
    """数值越界 (ge=1 / le=5): 文案区分「不能小于」与「不能大于」."""
    execution = await execute_tool(ECHO, arguments='{"message": "x", "count": 9}')
    assert not execution.ok
    assert "数值不能大于 5" in execution.error and "实际: 9" in execution.error
    execution = await execute_tool(ECHO, arguments='{"message": "x", "count": 0}')
    assert not execution.ok
    assert "数值不能小于 1" in execution.error and "实际: 0" in execution.error


async def test_gt_boundary_included_with_strict_wording():
    """gt=0 越界: 界值进文案且措辞为「必须大于」 (gt 与 ge 语义区分)."""
    execution = await execute_tool(
        RECORDS,
        arguments='{"items": [{"value": 0, "unit": "meter"}]}',
    )
    assert not execution.ok
    assert "items.0.value" in execution.error
    assert "数值必须大于 0" in execution.error
    assert "实际: 0" in execution.error


async def test_unknown_field_lists_allowed_params():
    """未知参数: schema additionalProperties=False 的运行时一侧, 列出可用参数."""
    execution = await execute_tool(ECHO, arguments='{"message": "x", "extra": 1}')
    assert not execution.ok
    assert "未知参数" in execution.error
    assert "extra" in execution.error and "message" in execution.error


async def test_nested_validation_error_reports_path():
    """嵌套校验错误: loc 路径 (items.0.value) 可见, 可操作."""
    execution = await execute_tool(
        RECORDS,
        arguments='{"items": [{"value": -1, "unit": "meter"}]}',
    )
    assert not execution.ok
    assert "items.0.value" in execution.error
    assert "实际: -1" in execution.error
    assert "Traceback" not in execution.error


# ---------------------------------------------------------------------------
# 异常语义 (验收项 3)
# ---------------------------------------------------------------------------


async def test_actionable_error_message_passthrough():
    """作者 raise ToolActionableError: 消息原文透传, 触发模型自纠错."""
    execution = await execute_tool(BUSINESS, arguments='{"order_no": "abc"}')
    assert not execution.ok
    assert execution.error == "order_no 应为 14 位数字, 实际 'abc', 请核对后重试"
    assert isinstance(execution.exception, ToolActionableError)


async def test_unexpected_exception_not_leaked_to_model():
    """意外异常: 通用内部错误文案, traceback 不外泄, 根因保留 exception."""
    execution = await execute_tool(BOOM, arguments='{"dividend": 1, "divisor": 0}')
    assert not execution.ok
    assert "内部错误" in execution.error
    assert "ZeroDivisionError" not in execution.error
    assert "Traceback" not in execution.error
    assert isinstance(execution.exception, ZeroDivisionError)


# ---------------------------------------------------------------------------
# manual 引擎形状检查 (无 pydantic 校验模型的兜底)
# ---------------------------------------------------------------------------


async def test_manual_missing_required_is_actionable():
    """manual 缺必填参数: 可操作提示 (而非误导性的「内部错误」)."""
    execution = await execute_tool(MANUAL_ECHO, arguments="{}")
    assert not execution.ok
    assert "缺少必填参数 order_no" in execution.error
    assert "内部错误" not in execution.error


async def test_manual_unknown_param_is_actionable():
    """manual 未知参数: 列出可用参数, 而非落入内部错误分支."""
    execution = await execute_tool(
        MANUAL_ECHO, arguments='{"order_no": "x", "extra": 1}'
    )
    assert not execution.ok
    assert "未知参数 ['extra']" in execution.error
    assert "order_no" in execution.error and "fuzzy" in execution.error
    assert "内部错误" not in execution.error


async def test_manual_shape_check_passes_through():
    """manual 形状合法: 直接透传给函数 (类型约束由作者函数内承担)."""
    execution = await execute_tool(MANUAL_ECHO, arguments='{"order_no": "A123"}')
    assert execution.ok and execution.content == "ok:A123"


# ---------------------------------------------------------------------------
# ValidationError type → 期望类型提示 (常见类型错误)
# ---------------------------------------------------------------------------


async def test_int_from_float_reports_integer_hint():
    """float 传给 int 注解参数: 类型错误提示期望整数."""
    execution = await execute_tool(ECHO, arguments='{"message": "x", "count": 2.5}')
    assert not execution.ok
    assert "类型错误: 期望 整数 (int)" in execution.error
    assert "实际: 2.5" in execution.error


# ---------------------------------------------------------------------------
# 工具级超时 (#15): 到点不再等, 但如实说清「等的是什么」
# ---------------------------------------------------------------------------


async def test_async_tool_timeout_backfills_actionable_text():
    """协程工具永不返回: 到点中止, 回填「发生了什么 + 结果未知」而不是空结果."""
    started = time.perf_counter()
    execution = await execute_tool(HANG_ASYNC, arguments="{}")
    elapsed = time.perf_counter() - started

    assert not execution.ok
    assert execution.content == "", "超时不许伪装成「工具返回了空结果」"
    assert "内部错误" not in execution.error, "超时不是意外故障, 有自己的文案"
    assert "执行超过 0.05 秒" in execution.error
    assert "结果未知" in execution.error, "结果可能已生效, 这层如实要说"
    assert "不要直接重试" in execution.error, "有副作用的工具可能已经生效, 要当场提醒"
    assert isinstance(execution.exception, ToolTimeoutError)
    assert execution.timed_out is True, "框架那道闸掐的: loop 据它中断本次运行"
    assert elapsed < 1, f"它是「到点就回」的, 实测等了 {elapsed:.2f} 秒"


async def test_sync_tool_timeout_leaves_its_thread_running(log_stream):
    """同步工具超时只能「不再等它」: 线程照跑到函数自己返回, 日志如实记一条."""
    finished: list[str] = []

    def blocking(
        seconds: Annotated[float, Field(description="占住线程的秒数")],
    ) -> str:
        """同步阻塞载体 (跑在线程池里, 超时掐不掉)."""
        time.sleep(seconds)
        finished.append("done")
        return "醒了"

    blocking_tool = tool(blocking, name="blocking_tool", timeout=0.05)

    execution = await execute_tool(blocking_tool, arguments='{"seconds": 0.5}')

    assert not execution.ok and "执行超过 0.05 秒" in execution.error
    assert execution.timed_out is True, "同步那条也是框架掐的 (线程还跑着不代表没超时)"
    assert finished == [], "已经放弃等待了 —— 函数这时还没跑完"

    records = [json.loads(line) for line in log_stream.getvalue().splitlines()]
    record = next(item for item in records if item.get("tool") == "blocking_tool")
    assert record["thread_still_running"] is True, "这条边界要如实记下来"
    assert "线程" in record["msg"]

    await asyncio.sleep(0.7)
    assert finished == ["done"], (
        "线程没停 —— 它一直跑到函数自己返回 (日志说的正是这件事)"
    )


async def test_tool_without_timeout_uses_the_global_default(monkeypatch):
    """没写 timeout 的工具取全局配置 —— 工具级缺省 → 全局默认这条链真的通."""
    monkeypatch.setattr(executor_module, "DEFAULT_TOOL_TIMEOUT_SECONDS", 0.05)

    execution = await execute_tool(HANG_ASYNC_BY_DEFAULT, arguments="{}")

    assert not execution.ok and "执行超过 0.05 秒" in execution.error


async def test_a_tools_own_timeout_error_is_not_reported_as_a_tool_timeout():
    """工具自己抛的 TimeoutError 不是框架掐的: 走意外异常那条路, 不谎报超时."""
    execution = await execute_tool(SELF_TIMEOUT, arguments="{}")

    assert not execution.ok
    assert "执行超过" not in execution.error
    assert "内部错误" in execution.error
    assert isinstance(execution.exception, TimeoutError)
    assert execution.timed_out is False, "工具报的故障不等于「框架放弃等待」"


async def test_a_tool_raising_the_timeout_type_is_an_unknown_outcome():
    """工具作者按同一语义抛 `ToolTimeoutError`: 类型即声明 —— 同样算结果未知.

    与上一条的分界: 内置 `TimeoutError` 是「它报了一个故障」(走意外异常文案,
    运行照旧), 而 `ToolTimeoutError` 是「这次调用的结果不知道」—— 工具自己抛它
    时说的是同一件事, 于是 loop 同样中断本次运行 (ADR-0024).
    """
    execution = await execute_tool(DECLARED_TIMEOUT, arguments="{}")

    assert not execution.ok
    assert execution.error == "下游连接读超时, 结果未知", "作者的话原样透传"
    assert execution.timed_out is True


async def test_the_timeout_guard_does_not_swallow_the_kill_switch():
    """工具执行中按停: CancelledError 原样传出, 工具不重跑 (#3) —— 超时闸不吞它."""
    entered = asyncio.Event()
    runs: list[str] = []

    async def slow_tool() -> str:
        """慢慢返回的载体 (取消必须从这里打断它)."""
        runs.append("started")
        entered.set()
        await asyncio.sleep(10)
        return "太晚了"

    slow = tool(slow_tool, name="slow_tool", timeout=30)
    task = asyncio.create_task(execute_tool(slow, arguments="{}"))

    await entered.wait()  # 工具已经开跑, 取消要打断的正是它
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runs == ["started"], "取消不是重试的触发点: 工具从头到尾只被调过一次"
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pending == [], "取消传播干净, 没有留下后台任务"


# ---------------------------------------------------------------------------
# timeout 是工具级配置 (注册期校验)
# ---------------------------------------------------------------------------


def test_timeout_is_configured_per_tool():
    """timeout 按工具给: 显式给了用它, 没给是 None (缺省 → 全局配置)."""
    assert tool(_echo_sync, timeout=2.5).timeout == 2.5
    assert tool(_echo_sync).timeout is None


def test_timeout_does_not_change_the_existing_tool_signature():
    """既有构造签名一个字没动: 前七个字段照旧, timeout 只能按关键字给."""
    built = Tool(
        _echo_sync.__name__,
        "说明",
        _echo_sync,
        {"type": "object"},
        None,
        None,
        {"writes": True},
    )
    assert built.timeout is None, "老写法不写 timeout 也能构造 (取全局默认)"

    with pytest.raises(TypeError):
        Tool(_echo_sync.__name__, "说明", _echo_sync, {}, None, None, {}, 1.0)


@pytest.mark.parametrize("bad", [0, -1, "30"])
def test_a_bad_timeout_is_a_registration_error(bad):
    """超时必须是正数秒 —— 配置错误在注册期当场报, 不留到运行时才现形."""
    with pytest.raises(ToolConfigError):
        tool(_echo_sync, timeout=bad)
