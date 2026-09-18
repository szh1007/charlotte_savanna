"""轨迹断言 + 确定性断言 (#62 #61).

**为什么断言轨迹**: 只看最终答案的用例会放过「答案对、过程错」的回归 —— 比如
模型本该先查订单再申请退款, 实现却把两个工具并发反了序; 或者参数在回填路上被
悄悄改写 (order_no 少了前导零). 轨迹是这类回归唯一看得见的地方 (#62 golden
轨迹断言: 断言「调了哪些工具、什么顺序、什么参数」).

**数据源**: MockLLM 的两半记录 —— `calls` (模型每轮看到了什么) 与
`responses` (模型每轮决定了什么); 被测代码零改动, 也不需要额外插桩.

用法::

    trace = trace_of(model)                       # 从 MockLLM 记的轨迹建视图
    trace.assert_tool_calls([("query_order", {"order_no": "20260701123456"})])
    trace.assert_turn_count(2)
    trace.assert_tool_result_backfilled("query_order", contains="已发货")
    trace.assert_pinned_sampling()                # 确定性 (#61)

期望项的两种写法: 字符串 = 只断言工具名; 二元组 (名字, 参数字典) = 名字 +
参数**子集**匹配 (JSON 里多出的键不影响断言; 少键或值不符即失败). 参数是畸形
JSON 时 `args` 为 None, 那种情况用 `trace.tool_calls[i].arguments` 断言原文.

大白话版: 这是「过程录像的验货单」—— 不看你最后答得对不对, 只看你中间干了
哪几件事、按什么顺序、拿什么参数干的. 失败时把「实际 vs 期望」并排打出来,
省得对着断言自己去猜跑成什么样.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from CharAgent.model.utils.types import ModelMessage, ModelResponse

# 确定性采样 (#61): temperature=0 + 固定 seed, 保证同输入同输出.
# 限定 (官方语义, 见 model/protocol.py): 思考模式下 temperature 不生效、
# top_p 下限 0.95, seed 仅保证 content 可复现 (reasoning 每次不同) —— 于是
# 快照 / 回放这类要求逐字复现的用例一律走非思考模式.
PINNED_TEMPERATURE = 0.0
PINNED_SEED = 20260914


def pinned_sampling() -> dict[str, Any]:
    """确定性采样参数 (构造 AgentLoop 时 `**pinned_sampling()` 展开传入)."""
    return {"temperature": PINNED_TEMPERATURE, "seed": PINNED_SEED}


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """轨迹里的一次工具调用 (来源是模型响应, 顺序即模型决策顺序).

    attributes:
        turn: 第几次模型决策 (1 起).
        id: tool_call_id (与回填的 tool 消息配对, #10).
        name: 工具名.
        arguments: 参数原文 (JSON 字符串, 保真不预解析).
        args: 参数解析结果; 畸形 JSON 为 None (#2 的纠错路径信号).
    """

    turn: int
    id: str
    name: str
    arguments: str
    args: dict[str, Any] | None

    def describe(self) -> str:
        """一行摘要 (断言失败时的「实际」列): `名字(参数原文)`."""
        return f"{self.name}({self.arguments})"


def parse_arguments(arguments: str) -> dict[str, Any] | None:
    """参数原文 → dict; 畸形 JSON 或非对象返回 None (不抛错, 交给断言说事)."""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class Trace:
    """一段模型调用记录 → 轨迹视图 (#62).

    通常由 `trace_of(model)` 建; 同一实例跨多次 run 时用 start / end 切片,
    每段轨迹的 turn 从 1 重新数.

    attributes:
        calls: 请求记录 (模型每轮看到什么).
        responses: 响应 (模型每轮决定了什么), 与 calls 一一对应.
    """

    def __init__(
        self,
        calls: Sequence[dict[str, Any]],
        responses: Sequence[ModelResponse],
    ) -> None:
        if len(calls) != len(responses):
            raise AssertionError(
                f"轨迹的请求与响应条数不匹配 ({len(calls)} vs {len(responses)}): "
                f"数据源坏了, 不是被测代码的问题"
            )
        if not calls:
            raise AssertionError(
                "轨迹是空的 (模型一次都没被调用): 检查用例是否真的跑起来了"
            )
        self.calls: list[dict[str, Any]] = list(calls)
        self.responses: list[ModelResponse] = list(responses)

    # ------------------------------------------------------------------
    # 取数
    # ------------------------------------------------------------------

    @property
    def turn_count(self) -> int:
        """模型决策次数 (Turn 数, 概念见 agent/loop.py)."""
        return len(self.calls)

    @property
    def tool_calls(self) -> list[ToolCallRecord]:
        """按顺序摊平的全部工具调用 (含同一轮的并行调用, 顺序即响应内顺序)."""
        records: list[ToolCallRecord] = []
        for turn, response in enumerate(self.responses, start=1):
            for call in response.tool_calls:
                records.append(
                    ToolCallRecord(
                        turn=turn,
                        id=call.id,
                        name=call.name,
                        arguments=call.arguments,
                        args=parse_arguments(call.arguments),
                    )
                )
        return records

    @property
    def tool_names(self) -> list[str]:
        """按顺序的全部工具名 (顺序断言最常用的那一列)."""
        return [record.name for record in self.tool_calls]

    @property
    def contents(self) -> list[str | None]:
        """每轮响应的正文 (含工具轮的过程叙述; 断「说过什么」用)."""
        return [response.content for response in self.responses]

    def seen(self, turn: int) -> list[ModelMessage]:
        """第 `turn` 轮 (1 起) 模型看到的消息历史 (浅拷贝的冻结快照)."""
        if not 1 <= turn <= self.turn_count:
            raise AssertionError(
                f"第 {turn} 轮不存在: 本次轨迹只有 {self.turn_count} 轮"
            )
        return list(self.calls[turn - 1]["messages"])

    def sampling(self, turn: int) -> dict[str, Any]:
        """第 `turn` 轮实际用的采样参数 (temperature / seed / thinking 等)."""
        if not 1 <= turn <= self.turn_count:
            raise AssertionError(
                f"第 {turn} 轮不存在: 本次轨迹只有 {self.turn_count} 轮"
            )
        record = self.calls[turn - 1]
        return {
            key: record.get(key)
            for key in ("temperature", "top_p", "seed", "thinking", "reasoning_effort")
        }

    def describe(self) -> str:
        """整段轨迹的稳定文本 (失败信息与「两次 run 是否一致」的比较都用它)."""
        lines = []
        for turn, response in enumerate(self.responses, start=1):
            calls = " | ".join(f"{c.name}({c.arguments})" for c in response.tool_calls)
            lines.append(
                f"turn {turn}: content={response.content!r} "
                f"tool_calls=[{calls}] finish={response.finish_reason.value}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 断言
    # ------------------------------------------------------------------

    def assert_turn_count(self, expected: int) -> None:
        """断言模型决策次数 (轮数不符 = 循环多跑或少跑了)."""
        if self.turn_count != expected:
            raise AssertionError(
                f"模型决策次数不符: 期望 {expected} 轮, 实际 {self.turn_count} 轮\n"
                f"{self.describe()}"
            )

    def assert_no_tool_calls(self) -> None:
        """断言全程没调工具 (纯文本路径)."""
        if self.tool_calls:
            raise AssertionError(
                f"期望不调工具, 实际调了 {len(self.tool_calls)} 次: "
                f"{[record.describe() for record in self.tool_calls]}"
            )

    def assert_tool_names(self, expected: Sequence[str], *, exact: bool = True) -> None:
        """断言工具调用顺序.

        Args:
            expected: 期望的工具名序列.
            exact: True (默认) 条数与顺序都必须一致; False 表示只要求期望是
                实际序列的**前缀** (后面还调了别的工具不算错).
        """
        actual = self.tool_names
        wanted = list(expected)
        ok = actual == wanted if exact else actual[: len(wanted)] == wanted
        if not ok:
            raise AssertionError(self._mismatch_message("工具名序列", wanted, actual))

    def assert_tool_calls(
        self,
        expected: Sequence[str | tuple[str, Mapping[str, Any]]],
        *,
        exact: bool = True,
    ) -> None:
        """断言工具调用顺序与参数 (#62 的核心断言).

        Args:
            expected: 期望序列, 每项是工具名 (只断名字) 或
                `(工具名, 参数字典)` (名字 + 参数子集匹配).
            exact: 见 assert_tool_names.

        Raises:
            AssertionError: 条数 / 顺序 / 名字 / 参数任一不符, 信息里给出
                「期望 vs 实际」与第一条不符的具体原因.
        """
        wanted = list(expected)
        records = self.tool_calls
        if exact and len(records) != len(wanted):
            raise AssertionError(
                self._mismatch_message(
                    "工具调用条数",
                    [self._expect_text(item) for item in wanted],
                    [record.describe() for record in records],
                )
            )
        if not exact and len(records) < len(wanted):
            raise AssertionError(
                self._mismatch_message(
                    "工具调用条数 (期望是前缀)",
                    [self._expect_text(item) for item in wanted],
                    [record.describe() for record in records],
                )
            )
        for index, item in enumerate(wanted):
            reason = self._compare(records[index], item)
            if reason is not None:
                raise AssertionError(
                    self._mismatch_message(
                        f"工具调用轨迹 (第 {index + 1} 项不符: {reason})",
                        [self._expect_text(entry) for entry in wanted],
                        [record.describe() for record in records],
                    )
                )

    def assert_tool_result_backfilled(
        self,
        tool_name: str,
        *,
        contains: str | None = None,
    ) -> None:
        """断言某个工具的结果被回填进了下一轮的 wire 历史 (#10 的配对约束).

        回填是「模型能看到工具结果」的唯一途径 —— 工具跑了但结果没回填,
        模型下一轮就会重复调用同一个工具 (无限循环的常见成因).

        Args:
            tool_name: 工具名 (取它的第一次调用).
            contains: 回填内容须包含的片段 (None 表示只断存在).
        """
        record = self._first_call(tool_name)
        if record.turn >= self.turn_count:
            raise AssertionError(
                f"工具 {tool_name} 的调用是最后一轮 ({record.turn}) 发出的, "
                f"没有任何后续请求携带它的结果 (轨迹终止在这里):\n{self.describe()}"
            )
        backfilled = [
            message
            for message in self.seen(record.turn + 1)
            if message.get("role") == "tool"
            and message.get("tool_call_id") == record.id
        ]
        if not backfilled:
            raise AssertionError(
                f"工具 {tool_name} (id={record.id}) 的结果没有回填到第 "
                f"{record.turn + 1} 轮的请求里:\n{self.describe()}"
            )
        if contains is not None:
            content = str(backfilled[0].get("content") or "")
            if contains not in content:
                raise AssertionError(
                    f"工具 {tool_name} 的回填内容不含 {contains!r}, 实际: "
                    f"{content[:200]!r}"
                )

    def assert_pinned_sampling(
        self,
        *,
        temperature: float | None = PINNED_TEMPERATURE,
        seed: int | None = PINNED_SEED,
    ) -> None:
        """断言每一轮都用了钉死的采样参数 (#61 确定性的前提).

        Args:
            temperature: 期望温度 (默认 0.0).
            seed: 期望种子 (默认 PINNED_SEED).

        Raises:
            AssertionError: 某一轮的参数不符 —— 漏传 seed / temperature 会让
                「同输入同输出」失效, 快照与回放用例就会变成偶发.
        """
        for turn in range(1, self.turn_count + 1):
            actual = self.sampling(turn)
            if actual["temperature"] != temperature or actual["seed"] != seed:
                raise AssertionError(
                    f"第 {turn} 轮的采样参数没有钉死: 期望 temperature="
                    f"{temperature} / seed={seed}, 实际 {actual} "
                    f"(用 pinned_sampling() 构造 loop, #61)"
                )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _first_call(self, tool_name: str) -> ToolCallRecord:
        """取某个工具的第一次调用 (没有就报错, 说清实际调过什么)."""
        for record in self.tool_calls:
            if record.name == tool_name:
                return record
        raise AssertionError(
            f"轨迹里没有工具 {tool_name!r} 的调用, 实际调过: {self.tool_names}"
        )

    def _compare(
        self,
        record: ToolCallRecord,
        expected: str | tuple[str, Mapping[str, Any]],
    ) -> str | None:
        """比对一项期望; 返回 None 表示相符, 否则返回不符的原因."""
        if isinstance(expected, str):
            if record.name != expected:
                return f"期望工具名 {expected!r}, 实际 {record.name!r}"
            return None
        name, wanted_args = expected
        if record.name != name:
            return f"期望工具名 {name!r}, 实际 {record.name!r}"
        if record.args is None:
            return f"参数不是合法 JSON 对象, 无法按参数断言: {record.arguments!r}"
        for key, wanted in wanted_args.items():
            if key not in record.args:
                return f"参数缺少键 {key!r} (实际 {record.args})"
            if record.args[key] != wanted:
                return f"参数 {key!r} 不符: 期望 {wanted!r}, 实际 {record.args[key]!r}"
        return None

    def _expect_text(self, expected: str | tuple[str, Mapping[str, Any]]) -> str:
        """期望项 → 展示文本 (与 ToolCallRecord.describe 同一版式, 便于并排看)."""
        if isinstance(expected, str):
            return expected
        name, args = expected
        return f"{name}({json.dumps(args, ensure_ascii=False)})"

    def _mismatch_message(
        self,
        headline: str,
        expected: Sequence[str],
        actual: Sequence[str],
    ) -> str:
        """失败信息统一版式: 标题 + 期望 + 实际 + 分轮轨迹."""
        return (
            f"{headline}\n"
            f"  期望 ({len(expected)}): {' -> '.join(expected) or '(空)'}\n"
            f"  实际 ({len(actual)}): {' -> '.join(actual) or '(空)'}\n"
            f"分轮轨迹:\n{self.describe()}"
        )


def trace_of(model: Any, *, start: int = 0, end: int | None = None) -> Trace:
    """从 MockLLM (或录制包装) 的记录里取出轨迹视图.

    Args:
        model: 带 `calls` / `responses` 两个记录的 fake 或录制包装.
        start / end: 切片 (同一实例跑多次 run 时按段取; 每段的 turn 从 1 重数).

    Returns:
        Trace: 轨迹视图.
    """
    calls = list(getattr(model, "calls", []))[start:end]
    responses = list(getattr(model, "responses", []))[start:end]
    return Trace(calls, responses)
