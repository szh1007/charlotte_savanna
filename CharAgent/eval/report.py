"""报告: 一批跑分 → JSON (程序读) + Markdown (人看, 七块).

一句话理解: 跑批器交上来的是**一堆事实**, 这个文件把它们**收成数** (汇总) 再
**排成两份东西** —— JSON 给 `compare` 与别的程序读, Markdown 给人看.

**为什么 Markdown 从 JSON 那份数据渲染** (而不是各写一遍): 两份东西一旦各算各的,
迟早出现「表上写 66.7% 而 JSON 里是 0.75」这种谁都不信的报告. 于是 `to_dict()` 是
**规范形状**, Markdown 只是它的一种排版, `compare` 读的也是它. 一处算, 两处看.

## 七块 (票据定的)

| # | 块 | 回答什么 |
|---|----|---------|
| 1 | 配置快照 | 这是**哪套参数**下的分 (不记就说不清) |
| 2 | 对照总表 | 两组各自的召回 / 准确 / 三分类 / 成本规模 |
| 3 | 波动 | 每组 3 次各自的数与极差 —— 「为什么要跑 3 次」的正当性 |
| 4 | 逐题表 | 每题每一跑实际调了什么、结局如何 |
| 5 | 失败样本 | badcase 回流的入口 |
| 6 | 差异归因 | 哪些题 A 好 B 差 (带通过率, 免得把噪声当改进) |
| 7 | 两类收益 | 准确率收益与成本收益**分开说** (口径不同, 混着说会串) |

## 三处口径, 读报告前先知道

- **判据的分子分母只数「跑完」的跑次** (`RunOutcome.COMPLETED`). 截断 / 挂起 / 坏了
  的那几次如实进分布与逐题表, 但**不进分数** —— 理由见 `utils/types.py` 的 docstring
  (全挂那组前缀更长, 更容易撞 token 预算; 算作答错就是拿前缀长度冒充选择能力).
- **平均轮数 / token / 耗时数的是全部跑次** (含没跑完的) —— 那些跑次一样花了钱,
  把它们剔掉会把成本说低. 于是它们与判据的分母**不同**, 表里分开列.
- **比率是池化的**: 把各次的分子分母分别相加再相除, 不是各个比率求平均 (见
  `Metric`). 零期望的样本没有分母, 单列计数、不进池.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from CharAgent.eval.runner import Attempt, EvalReport, GroupResult
from CharAgent.eval.utils.errors import EvalConfigError
from CharAgent.eval.utils.types import RunOutcome

# 落盘格式的版本号 (JSON 里那个 `schema_version` 键). 它变了 = 老 JSON 读出来的
# 东西可能与新代码的理解不同, 于是 `compare` 会当场拒绝而不是拿两套口径硬比一通.
#
# 名字带 REPORT_ 前缀是因为 `REPORT_SCHEMA_VERSION` 这个名字 checkpoint 那边已经占了
# (快照序列化的版本) —— 两个版本号管的是两件不相干的事, 同名会在根门面撞车.
REPORT_SCHEMA_VERSION = 1

# 三分类的中文说法 (报告里到处在用, 一处定义)
OUTCOME_LABELS: dict[str, str] = {
    RunOutcome.COMPLETED.value: "跑完",
    RunOutcome.TRUNCATED.value: "截断",
    RunOutcome.SUSPENDED.value: "挂起",
    RunOutcome.BROKEN.value: "坏了",
}

# 没数时的占位符 (与「0」和「0%」分得开)
DASH = "—"

# Markdown 表格里一格最多写多少个字 (题面那种长文本要截; 全写进去表就没法看了)
_CELL_LIMIT = 60

# 两张表下面那句说明 (口径不写在表边上, 读者就会按自己的默认理解去读那些数)
_DENOMINATOR_NOTE = (
    "判据的分子分母**只数跑完的跑次**, 平均轮数 / token / 耗时数的是全部跑次"
)
_DELTA_NOTE = (
    "差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), "
    "其余跟着该行的单位"
)


# ---------------------------------------------------------------------------
# 汇总 (从跑次算数, JSON 与 Markdown 共用)
# ---------------------------------------------------------------------------


def summarize_attempts(
    attempts: Sequence[Attempt], judges: Sequence[str]
) -> dict[str, Any]:
    """一批跑次 → 这一组的汇总数 (总表 / 波动 / 对照三处都用它).

    传入哪个切片就是哪一份汇总: 全部跑次 = 总表那一行; 只传第 N 跑的 = 波动那一列.

    Args:
        attempts: 要汇总的跑次 (顺序无所谓, 只是被数一遍).
        judges: 判据名的顺序 (报告表头按它排).

    Returns:
        dict: 可直接 json.dump 的汇总 (键见实现, 每个数都有明确分母).
    """
    total = len(attempts)
    outcomes = {item.value: 0 for item in RunOutcome}
    for attempt in attempts:
        outcomes[attempt.facts.outcome.value] += 1

    # 「跑完」才进判据的分母 (见模块 docstring 的第一条口径)
    counted = [item for item in attempts if item.facts.counted]
    zero_call = sum(1 for item in counted if not item.facts.tool_calls)
    errors = sum(len(item.judge_errors) for item in attempts)

    entry: dict[str, Any] = {
        "attempts": total,
        "counted": len(counted),
        "outcomes": outcomes,
        "zero_call": zero_call,
        "judge_errors": errors,
        # 成本规模那一行数**全部**跑次 (截断的那几次一样花了钱)
        "avg_turns": _mean([item.facts.turns for item in attempts]),
        "avg_tokens": _mean([item.facts.tokens for item in attempts]),
        "avg_elapsed_ms": _mean([item.facts.elapsed_ms for item in attempts]),
        "total_cost": _total_cost(attempts),
        "judges": _judge_summary(counted, judges),
    }
    return entry


def _judge_summary(counted: Sequence[Attempt], judges: Sequence[str]) -> dict[str, Any]:
    """每个判据在「跑完」的那批跑次上的分数 (通过数 + 池化子指标).

    Returns:
        dict: 判据名 -> `{ok, total, errors, metrics}`; `metrics` 里每个子指标是
            `{hit, total, rate, no_denominator}` —— `total` 为 0 的那些单列在
            `no_denominator` 里, 不进池 (零期望样本: 一个都没调对它是满分).
    """
    result: dict[str, Any] = {}
    for name in judges:
        passed = 0
        judged = 0
        failed_to_judge = 0
        hits: dict[str, float] = {}
        totals: dict[str, float] = {}
        denominatorless: dict[str, int] = {}
        for attempt in counted:
            if name in attempt.judge_errors:
                failed_to_judge += 1
                continue
            judgment = attempt.judgments.get(name)
            if judgment is None:
                continue
            judged += 1
            passed += 1 if judgment.ok else 0
            for key, metric in judgment.metrics.items():
                if metric.total == 0:
                    denominatorless[key] = denominatorless.get(key, 0) + 1
                    continue
                hits[key] = hits.get(key, 0.0) + metric.hit
                totals[key] = totals.get(key, 0.0) + metric.total
        result[name] = {
            "ok": passed,
            "total": judged,
            "errors": failed_to_judge,
            "metrics": {
                key: {
                    "hit": hits[key],
                    "total": totals[key],
                    "rate": hits[key] / totals[key],
                    "no_denominator": denominatorless.get(key, 0),
                }
                for key in hits
            },
        }
    return result


def _roll_up(attempts: Sequence[Attempt]) -> dict[str, Any]:
    """一道题跨跑次的汇总 (差异归因那块比的就是这里的 `rate`).

    `rate` 的分母是「跑完的跑次」而非全部 —— 一道题 3 跑里 1 跑截断, 它的通过率
    是另外 2 跑里过了几个. 分母为 0 时给 None, 于是对照表里显示 `—` 而不是
    `0%` (「没跑完」与「一跑没过」必须分得开).
    """
    counted = [item for item in attempts if item.facts.counted and item.judgments]
    passed = sum(1 for item in counted if all(j.ok for j in item.judgments.values()))
    outcomes = {item.value: 0 for item in RunOutcome}
    for attempt in attempts:
        outcomes[attempt.facts.outcome.value] += 1
    return {
        "attempts": len(attempts),
        "counted": len(counted),
        "passed": passed,
        "rate": (passed / len(counted)) if counted else None,
        "outcomes": outcomes,
    }


def _mean(values: Sequence[float]) -> float | None:
    """平均值; 一个数都没有时给 None (不是 0)."""
    return (sum(values) / len(values)) if values else None


def _total_cost(attempts: Sequence[Attempt]) -> float | None:
    """总金额; 一跑都没算出来时给 None (不是 0).

    「没配价目表」与「这一批一分钱没花」在报告里必须分得开 —— 后者不可能发生
    (跑一次就至少几个 token), 而前者是配置问题.
    """
    known = [item.facts.cost for item in attempts if item.facts.cost is not None]
    return sum(known) if known else None


# ---------------------------------------------------------------------------
# JSON 形状 (规范形状: Markdown 与 compare 都读它)
# ---------------------------------------------------------------------------


def report_to_dict(report: EvalReport) -> dict[str, Any]:
    """整份报告 → 规范形状的字典 (顶层)."""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": report.generated_at.isoformat(),
        "times": report.times,
        "judges": list(report.judges),
        "groups": [group_to_dict(group, report.judges) for group in report.groups],
    }


def group_to_dict(group: GroupResult, judges: Sequence[str]) -> dict[str, Any]:
    """一组 → 规范形状的字典.

    配置快照取**第一跑**交上来的那份; 若同组里出现了不一样的, 全部罗列在
    `config_conflicts` 里 —— 同一批用了两套参数这件事本身就该被看见, 而不是
    悄悄用第一份糊过去.
    """
    configs: list[dict[str, Any]] = []
    seen: list[str] = []
    for attempt in group.attempts:
        snapshot = dict(attempt.facts.config)
        if not snapshot:
            continue
        stamp = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if stamp not in seen:
            seen.append(stamp)
            configs.append(snapshot)

    entry: dict[str, Any] = {
        "name": group.name,
        "judges": list(judges),
        "config": configs[0] if configs else {},
        "summary": summarize_attempts(group.attempts, judges),
        "fluctuation": _fluctuation(group.attempts, judges),
        "cases": _cases_to_dict(group.attempts),
    }
    if len(configs) > 1:
        entry["config_conflicts"] = configs
    badcases = _badcases(group.attempts)
    entry["badcases"] = badcases
    return entry


def _fluctuation(
    attempts: Sequence[Attempt], judges: Sequence[str]
) -> list[dict[str, Any]]:
    """按「第几跑」切片的汇总 (波动那一块的每一列). 按 index 升序."""
    indexes = sorted({item.index for item in attempts})
    return [
        summarize_attempts([item for item in attempts if item.index == index], judges)
        for index in indexes
    ]


def _cases_to_dict(attempts: Sequence[Attempt]) -> list[dict[str, Any]]:
    """逐题 → 规范形状 (每题一段, 内含它的全部跑次)."""
    cases: list[dict[str, Any]] = []
    seen: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        seen.setdefault(attempt.case.id, []).append(attempt)
    for case_id, items in seen.items():
        case = items[0].case
        cases.append(
            {
                "id": case.id,
                "question": case.question,
                "expect_tools": list(case.expect_tools),
                "expect_args": {
                    name: dict(args) for name, args in case.expect_args.items()
                },
                "meta": dict(case.meta),
                "attempts": [_attempt_to_dict(item) for item in items],
                "rolled": _roll_up(items),
            }
        )
    return cases


def _attempt_to_dict(attempt: Attempt) -> dict[str, Any]:
    """一次跑 → 规范形状 (判据结论与抛错都原样带上)."""
    facts = attempt.facts
    return {
        "index": attempt.index,
        "outcome": facts.outcome.value,
        "error": facts.error,
        "answer_chars": len(facts.answer) if facts.answer is not None else None,
        "tool_calls": [
            {
                "tool_name": call.tool_name,
                "arguments": call.arguments,
                "status": call.status.value,
                "duration_ms": call.duration_ms,
            }
            for call in facts.tool_calls
        ],
        "turns": facts.turns,
        "tokens": facts.tokens,
        "elapsed_ms": facts.elapsed_ms,
        "run_id": facts.run_id,
        "cost": facts.cost,
        "judgments": {
            name: {
                "ok": judgment.ok,
                "reason": judgment.reason,
                "metrics": {
                    key: {
                        "hit": metric.hit,
                        "total": metric.total,
                        "rate": metric.rate,
                    }
                    for key, metric in judgment.metrics.items()
                },
            }
            for name, judgment in attempt.judgments.items()
        },
        "judge_errors": dict(attempt.judge_errors),
    }


def _badcases(attempts: Sequence[Attempt]) -> list[dict[str, Any]]:
    """失败样本: **跑完了但没判过**的那些跑次 (回流的入口).

    没跑完的那几种 (截断 / 挂起 / 坏了) 刻意不在这里 —— 它们的「差在哪」是「没答完」,
    与「答了但答错」是两种修法. 它们照样逐条在逐题表与三分类分布里, 不会消失.
    """
    rows: list[dict[str, Any]] = []
    for attempt in attempts:
        if not attempt.judgeable:
            continue
        failed = [name for name, item in attempt.judgments.items() if not item.ok]
        if not failed:
            continue
        rows.append(
            {
                "case_id": attempt.case.id,
                "index": attempt.index,
                "failed_judges": failed,
                "reasons": {name: attempt.judgments[name].reason for name in failed},
                "tool_names": list(attempt.facts.tool_names),
                "outcome": attempt.facts.outcome.value,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Markdown: 七块
# ---------------------------------------------------------------------------


def render_markdown(data: Mapping[str, Any]) -> str:
    """规范形状 → Markdown (七块).

    Args:
        data: `report_to_dict` 的产物 (或从 JSON 读回来的同一形状).

    Returns:
        str: 整份 Markdown 文本.

    Raises:
        EvalConfigError: 组数不是一到两组 (对照表表达不了三组以上).
    """
    groups = list(data.get("groups") or ())
    _check_groups(groups)
    times = int(data.get("times") or 0)
    judges = [str(name) for name in data.get("judges") or ()]

    lines: list[str] = ["# 跑分报告", ""]
    lines.append(
        f"- 跑分时间: {data.get('generated_at', DASH)}"
        f" · 每题跑次: {times}"
        f" · 判据: {' / '.join(judges) or '(没配判据)'}"
        f" · 组数: {len(groups)}"
    )
    lines.append("")

    lines += _config_block(groups)
    lines += _summary_block(groups, times)
    lines += _fluctuation_block(groups, times)
    lines += _cases_block(groups)
    lines += _badcase_block(groups)
    if len(groups) == 2:
        lines += _attribution_block(groups[0], groups[1])
        lines += _gains_block(groups[0], groups[1])
    else:
        lines += [
            "## 6. 差异归因",
            "",
            "只有一组, 没有可对照的对象 —— 出第二份报告后用 `compare` 比, "
            "或者一次跑两组.",
            "",
            "## 7. 两类收益",
            "",
            "同上: 收益是**两组之间**的差, 一组时无从谈起.",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def render_comparison(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    """两份**单组**报告 → 对照那几块 (配置快照 / 总表 / 波动 / 差异归因 / 两类收益).

    `compare` 走的就是它 —— 于是文件对照与报告里的对照是同一条渲染路径, 不会一套
    表两副面孔. 编号沿用报告里的块号 (1 / 2 / 3 / 6 / 7), 于是这一页与整份报告能对上.

    **配置快照那一块非有不可**: 「改了 prompt 之后好没好」这类比法, 最要紧的正是
    「这两份各是哪套参数」—— 不写下来, 隔一周回看就说不清那个 +8 个百分点是哪两版
    之间的事.

    **波动那一块同理**: 这张表最容易被读错的地方, 就是把两次跑分之间的噪声当成
    改进 —— 摆出每一跑各自的数与极差, 读者才有得判断.

    Args:
        a: 规范形状里的**一组** (即 `data["groups"][i]`).
        b: 另一组.

    Returns:
        str: 那几块的 Markdown.
    """
    lines = [f"# 对照: {a.get('name', DASH)} vs {b.get('name', DASH)}", ""]
    lines += _config_block([a, b])
    lines += _summary_block([a, b], 0)
    lines += _fluctuation_block([a, b], _times_of(a, b))
    lines += _attribution_block(a, b)
    lines += _gains_block(a, b)
    return "\n".join(lines).rstrip() + "\n"


def _times_of(a: Mapping[str, Any], b: Mapping[str, Any]) -> int:
    """从两组各自记着的波动次数里读回「每题跑几次」(两组取大的那个)."""
    return max(len(a.get("fluctuation") or ()), len(b.get("fluctuation") or ()), 0)


def load_report(path: str) -> dict[str, Any]:
    """读一份落盘的 JSON 报告 (形状不对当场报错, 不猜).

    Raises:
        EvalConfigError: 文件读不了 / 不是 JSON / 不是本包认的形状 / 版本对不上.
    """
    file = Path(path)
    try:
        raw = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalConfigError(f"读不了这份报告: {file} ({exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvalConfigError(f"{file} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EvalConfigError(
            f"{file} 的顶层应该是 JSON 对象, 实际是 {type(data).__name__}"
        )
    version = data.get("schema_version")
    if version != REPORT_SCHEMA_VERSION:
        raise EvalConfigError(
            f"{file} 的 schema_version 是 {version!r}, 本包只认 "
            f"{REPORT_SCHEMA_VERSION} (版本不同 = 口径可能不同, 不硬比)"
        )
    _check_groups(list(data.get("groups") or ()))
    return data


def _check_groups(groups: Sequence[Mapping[str, Any]]) -> None:
    """组数必须在 1..2 (对照是两两比).

    Raises:
        EvalConfigError: 一组都没有 / 超过两组.
    """
    if not groups:
        raise EvalConfigError("这份报告一组都没有 (是不是把别的东西当报告读了?)")
    if len(groups) > 2:
        raise EvalConfigError(
            f"报告里有 {len(groups)} 组, 对照表只表达两两比 —— 请用 compare 逐对看"
        )


# --- 第 1 块: 配置快照 -------------------------------------------------------


def _config_block(groups: Sequence[Mapping[str, Any]]) -> list[str]:
    """配置快照: 键为行、组为列 (两组时一眼看出差在哪)."""
    columns = [str(group.get("name", DASH)) for group in groups]
    flat = [_flatten(group.get("config") or {}) for group in groups]
    keys: list[str] = []
    for item in flat:
        keys.extend(key for key in item if key not in keys)
    lines = ["## 1. 配置快照", ""]
    if not keys:
        lines += [
            "(两组都没交配置快照 —— 报告头这块就说不清「这是哪套参数下的分」)",
            "",
        ]
        return lines
    rows = [
        [key] + [_cell(flat[index].get(key)) for index in range(len(groups))]
        for key in keys
    ]
    lines += _table(["配置项", *columns], rows)
    for group in groups:
        conflicts = group.get("config_conflicts")
        if conflicts:
            lines.append(
                f"- **注意**: {group.get('name')} 这一组里出现了 {len(conflicts)} "
                f"份不同的配置快照 (下面摆的是第一份), 全部原文见 JSON 的 "
                f"`config_conflicts`"
            )
    lines.append("")
    return lines


def _flatten(config: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    """嵌套配置拍平一层 (`刹车.max_turns`), 好摆进一张两列的表."""
    flat: dict[str, str] = {}
    for key, value in config.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, prefix=f"{name}."))
        else:
            flat[name] = _cell(value)
    return flat


# --- 第 2 块: 对照总表 -------------------------------------------------------


def _summary_block(groups: Sequence[Mapping[str, Any]], times: int) -> list[str]:
    """对照总表: 一行一个指标, 一列一组 (+ 两组时右端再一列差).

    同型的表在报告里出现两次 (第 2 块与 compare 的开头), 于是抽在这里共用.
    """
    columns = [str(group.get("name", DASH)) for group in groups]
    heads = [_headline(group.get("summary") or {}) for group in groups]
    lines = ["## 2. 对照总表" if len(groups) > 1 else "## 2. 本组总表", ""]
    if times:
        lines.append(f"(每题跑 {times} 次. {_DENOMINATOR_NOTE})")
        lines.append("")
    rows: list[list[str]] = []
    for index, row in enumerate(heads[0]):
        cells = [row.label] + [item[index].text for item in heads]
        if len(groups) == 2:
            cells.append(_delta(heads[0][index], heads[1][index]))
        rows.append(cells)
    lines += _table(["指标"] + columns + (["差"] if len(groups) == 2 else []), rows)
    if len(groups) == 2:
        lines += ["", f"({_DELTA_NOTE})"]
    lines.append("")
    return lines


@dataclass(frozen=True, slots=True)
class _Row:
    """总表里的一行: 标签 + 显示文本 + 可比数值 + 单位 + 属于哪个判据.

    数值与显示**刻意分开**: `66.7%` 与 `0.667` 是同一件事的两种写法, 拿显示文本
    去算差会算错. 但**归属**不能从显示文本里读 (第 7 块要按判据挑行) —— 那是拿
    排版当标识, 改一个字就悄悄挑错, 所以 `judge` 单独存一份.

    attributes:
        label: 行标签 (表最左边那一列).
        text: 显示文本 (已按单位量好).
        value: 可比数值; None = 没数, 不算差也不算极差.
        unit: 单位种类 (决定差与极差怎么写, 见 `_STYLES`).
        judge: 这一行属于哪个判据; 空串 = 不是判据的行 (跑次 / 平均 token 那些).
    """

    label: str
    text: str
    value: float | None
    unit: _Unit
    judge: str = ""


def _headline(summary: Mapping[str, Any]) -> list[_Row]:
    """一份汇总 → 表里的那些行."""
    rows = [
        _Row(
            "跑次",
            _render(summary.get("attempts"), _Unit.COUNT),
            summary.get("attempts"),
            _Unit.COUNT,
        ),
        _Row(
            "计入判据",
            _render(summary.get("counted"), _Unit.COUNT),
            summary.get("counted"),
            _Unit.COUNT,
        ),
    ]
    outcomes = summary.get("outcomes") or {}
    for key, label in OUTCOME_LABELS.items():
        count = outcomes.get(key, 0)
        rows.append(_Row(label, _render(count, _Unit.COUNT), count, _Unit.COUNT))
    zero_call = summary.get("zero_call", 0)
    rows.append(
        _Row("零调用跑次", _render(zero_call, _Unit.COUNT), zero_call, _Unit.COUNT)
    )
    errors = summary.get("judge_errors", 0)
    rows.append(_Row("判据抛错", _render(errors, _Unit.COUNT), errors, _Unit.COUNT))

    for name, item in (summary.get("judges") or {}).items():
        ok = int(item.get("ok", 0))
        total = int(item.get("total", 0))
        # 一次判据都没跑成 (total 为 0) 时不给比率 —— 那是「没判」而不是「零分」
        rate = (ok / total) if total else None
        rows.append(
            _Row(
                f"{name} 通过",
                f"{ok}/{total} ({_render(rate, _Unit.RATIO)})",
                rate,
                _Unit.RATIO,
                name,
            )
        )
        for key, metric in (item.get("metrics") or {}).items():
            note = ""
            if metric.get("no_denominator"):
                note = f" · {metric['no_denominator']} 跑无分母"
            rows.append(
                _Row(
                    f"{name}·{key}",
                    f"{_render(metric.get('rate'), _Unit.RATIO)}{note}",
                    metric.get("rate"),
                    _Unit.RATIO,
                    name,
                )
            )
    for label, key, unit in (
        ("平均轮数", "avg_turns", _Unit.NUMBER),
        ("平均 token", "avg_tokens", _Unit.NUMBER),
        ("平均耗时", "avg_elapsed_ms", _Unit.SECONDS),
        ("总金额", "total_cost", _Unit.MONEY),
    ):
        rows.append(
            _Row(label, _render(summary.get(key), unit), summary.get(key), unit)
        )
    return rows


# --- 第 3 块: 波动 -----------------------------------------------------------


def _fluctuation_block(groups: Sequence[Mapping[str, Any]], times: int) -> list[str]:
    """波动: 每一跑各自算一遍, 右端给极差 (一组一张子表).

    这一块是**跑 3 次的正当性所在**: 没有它, 读者会把两次跑分之间的噪声当成改进.
    """
    lines = ["## 3. 波动", ""]
    if times:
        lines += [f"(每题跑 {times} 次. 极差 = 最大减最小, 只对能算的指标算)", ""]
    for group in groups:
        lines += [f"### {group.get('name', DASH)}", ""]
        items = list(group.get("fluctuation") or ())
        if not items:
            lines += ["(没有跑次, 波动无从谈起)", ""]
            continue
        heads = [_headline(item) for item in items]
        header = (
            ["指标"] + [f"第 {index + 1} 跑" for index in range(len(items))] + ["极差"]
        )
        rows = [
            [row.label]
            + [item[index].text for item in heads]
            + [_spread([item[index] for item in heads])]
            for index, row in enumerate(heads[0])
        ]
        lines += _table(header, rows)
        lines.append("")
    lines += [f"({_DELTA_NOTE})", ""]
    return lines


# --- 第 4 块: 逐题表 ---------------------------------------------------------


def _cases_block(groups: Sequence[Mapping[str, Any]]) -> list[str]:
    """逐题表: **一行一次跑** (3 次就是 3 行), 题号重复出现 (一组一张子表).

    按跑次摊平而不是把 3 次挤成一格: 三次的实际调用可能完全不同, 挤在一起就看不
    出「第 2 跑调错了」这种波动.
    """
    header = [
        "题号",
        "题面",
        "期望工具",
        "实际调用",
        "命中",
        "终局",
        "轮",
        "token",
        "耗时",
    ]
    lines = ["## 4. 逐题表", ""]
    for group in groups:
        lines += [f"### {group.get('name', DASH)}", ""]
        rows: list[list[str]] = []
        for case in group.get("cases") or ():
            rows += [_case_row(case, attempt) for attempt in case.get("attempts") or ()]
        lines += _table(header, rows) if rows else ["(一道题都没有)"]
        lines.append("")
    return lines


def _case_row(case: Mapping[str, Any], attempt: Mapping[str, Any]) -> list[str]:
    """逐题表的一行 (一次跑)."""
    judgments = attempt.get("judgments") or {}
    failed = [name for name, item in judgments.items() if not item.get("ok")]
    errors = list((attempt.get("judge_errors") or {}).keys())
    if errors:
        verdict = f"判据抛错: {', '.join(errors)}"
    elif not judgments:
        verdict = DASH
    elif failed:
        verdict = "差: " + ", ".join(failed)
    else:
        verdict = "全过"
    calls = attempt.get("tool_calls") or []
    return [
        f"{case.get('id')} #{attempt.get('index')}",
        _cell(case.get("question")),
        _cell(", ".join(case.get("expect_tools") or ()) or "(不调工具)"),
        _cell(", ".join(str(call.get("tool_name")) for call in calls) or "(没调)"),
        verdict,
        OUTCOME_LABELS.get(str(attempt.get("outcome")), DASH),
        str(attempt.get("turns", 0)),
        str(attempt.get("tokens", 0)),
        _render(attempt.get("elapsed_ms"), _Unit.SECONDS),
    ]


# --- 第 5 块: 失败样本 -------------------------------------------------------


def _badcase_block(groups: Sequence[Mapping[str, Any]]) -> list[str]:
    """失败样本: 跑完了但没判过的那几跑 (回流的入口; 一组一张子表)."""
    lines = ["## 5. 失败样本", ""]
    for group in groups:
        badcases = list(group.get("badcases") or ())
        lines += [f"### {group.get('name', DASH)}", ""]
        if not badcases:
            lines += ["(没有 —— 跑完的跑次全过了)", ""]
        else:
            rows = [
                [
                    f"{item.get('case_id')} #{item.get('index')}",
                    _cell(
                        " / ".join(
                            str(name) for name in item.get("failed_judges") or ()
                        )
                    ),
                    _cell(_reasons(item.get("reasons"))),
                    _cell(", ".join(item.get("tool_names") or ()) or "(没调工具)"),
                    OUTCOME_LABELS.get(str(item.get("outcome")), DASH),
                ]
                for item in badcases
            ]
            lines += _table(
                ["题号", "差在哪个判据", "差在哪", "实际调用", "终局"], rows
            )
            lines.append("")
        others = _unfinished(group.get("summary") or {})
        if others:
            lines += [f"({others} —— 它们不是答错, 逐题表里逐条可见)", ""]
    return lines


def _reasons(reasons: Mapping[str, Any] | None) -> str:
    """判据名 -> 那句话, 拼成一行 (空话术不占位置)."""
    if not reasons:
        return ""
    return (
        " / ".join(
            f"{name}: {text}" for name, text in reasons.items() if str(text).strip()
        )
        or "(判据没给理由)"
    )


def _unfinished(summary: Mapping[str, Any]) -> str:
    """没跑完的那几种的一句话小结 (第 5 块末尾那一行); 一个都没有就是空串."""
    outcomes = summary.get("outcomes") or {}
    parts = [
        f"{label} {outcomes.get(key, 0)}"
        for key, label in OUTCOME_LABELS.items()
        if key != RunOutcome.COMPLETED.value and outcomes.get(key, 0)
    ]
    return "没跑完的跑次: " + ", ".join(parts) if parts else ""


# --- 第 6 块: 差异归因 -------------------------------------------------------


def _attribution_block(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """差异归因: 只列**两边通过率不同**的题, 按差距从大到小.

    为什么带通过率而不是只列「A 对 B 错」: 3 跑里 A 过 3 次而 B 只过 1 次, 与
    A 过 1 次 B 过 0 次, 完全是两种证据强度 —— 只标「对 / 错」会把它们写成同一件事,
    而那正是「把噪声当改进」的来源.
    """
    name_a = str(a.get("name", "A"))
    name_b = str(b.get("name", "B"))
    rolled_a = {
        case.get("id"): case.get("rolled") or {} for case in a.get("cases") or ()
    }
    rolled_b = {
        case.get("id"): case.get("rolled") or {} for case in b.get("cases") or ()
    }
    shared = [key for key in rolled_a if key in rolled_b]
    lines = ["## 6. 差异归因", ""]
    if not shared:
        lines += ["(两组没有共用的题号, 对不起来)", ""]
        return lines
    differing: list[tuple[str, _Row, _Row]] = []
    for key in shared:
        left = _Row(
            key,
            _render(rolled_a[key].get("rate"), _Unit.RATIO),
            rolled_a[key].get("rate"),
            _Unit.RATIO,
        )
        right = _Row(
            key,
            _render(rolled_b[key].get("rate"), _Unit.RATIO),
            rolled_b[key].get("rate"),
            _Unit.RATIO,
        )
        if left.value != right.value:
            differing.append((key, left, right))
    if not differing:
        lines += [f"({len(shared)} 道题的通过率两边完全一致 —— 这一批分不出高下)", ""]
        return lines
    # 有一边根本没跑完过的 (差值为 None) 排在最后 —— 它们不是「零分」
    differing.sort(
        key=lambda item: (
            abs((item[1].value or 0.0) - (item[2].value or 0.0))
            if None not in (item[1].value, item[2].value)
            else -1
        ),
        reverse=True,
    )
    rows = [
        [key, left.text, right.text, _delta(left, right)]
        for key, left, right in differing
    ]
    lines += _table(["题号", f"{name_a} 通过率", f"{name_b} 通过率", "差"], rows)
    lines += ["", f"({_DELTA_NOTE})"]
    won_a = [key for key, left, right in differing if _better(left, right) < 0]
    won_b = [key for key, left, right in differing if _better(left, right) > 0]
    lines += [
        "",
        f"- {name_a} 更好的题 ({len(won_a)}): {', '.join(won_a) or '(无)'}",
        f"- {name_b} 更好的题 ({len(won_b)}): {', '.join(won_b) or '(无)'}",
        "",
    ]
    return lines


def _better(earlier: _Row, later: _Row) -> int:
    """谁更好: 1 = 后面那组, -1 = 前面那组, 0 = 打平或不比较.

    有一边没数 (那次全是没跑完的跑次) 时判为 0 —— 那不是「零分」, 不该进任何一边
    的名单.
    """
    if earlier.value is None or later.value is None:
        return 0
    if later.value > earlier.value:
        return 1
    return -1 if later.value < earlier.value else 0


# --- 第 7 块: 两类收益 -------------------------------------------------------


def _gains_block(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """两类收益**分开说**: 准确率收益与成本收益口径不同, 混着说会串.

    准确率那一半只在「跑完的跑次」里比 (判据本来就只数那些); 成本那一半数全部跑次
    (截断的那几次一样花了钱). 两张表, 各自的表头写明分母.
    """
    name_a = str(a.get("name", "A"))
    name_b = str(b.get("name", "B"))
    lines = ["## 7. 两类收益", "", "### 准确率收益 (只在跑完的样本内比)", ""]
    heads = [_headline(item.get("summary") or {}) for item in (a, b)]
    # 只挑与准确率有关的那几行: 判据的通过率与其子指标 —— 认的是行上那个 `judge`,
    # 不是标签的文字 (拿排版当标识, 改一个字就会悄悄挑错行)
    rows = [
        [
            row.label,
            heads[0][index].text,
            heads[1][index].text,
            _delta(heads[0][index], heads[1][index]),
        ]
        for index, row in enumerate(heads[0])
        if row.judge
    ]
    if not rows:
        lines += ["(没配判据, 这一半无从谈起)", ""]
    else:
        lines += _table(["指标", name_a, name_b, "差"], rows)
        lines += ["", f"({_DELTA_NOTE})", ""]

    # 成本这张表没有比率行, 就不用再挂一遍差那一列的说明
    lines += ["### 成本收益 (数的是全部跑次, 含没跑完的)", ""]
    summary_a = a.get("summary") or {}
    summary_b = b.get("summary") or {}
    cost_rows = [
        _cost_row(
            "平均轮数",
            summary_a.get("avg_turns"),
            summary_b.get("avg_turns"),
            _Unit.NUMBER,
        ),
        _cost_row(
            "平均 token",
            summary_a.get("avg_tokens"),
            summary_b.get("avg_tokens"),
            _Unit.NUMBER,
        ),
        _cost_row(
            "平均耗时",
            summary_a.get("avg_elapsed_ms"),
            summary_b.get("avg_elapsed_ms"),
            _Unit.SECONDS,
        ),
        _cost_row(
            "总金额",
            summary_a.get("total_cost"),
            summary_b.get("total_cost"),
            _Unit.MONEY,
        ),
        _cost_row(
            "截断跑次",
            (summary_a.get("outcomes") or {}).get(RunOutcome.TRUNCATED.value, 0),
            (summary_b.get("outcomes") or {}).get(RunOutcome.TRUNCATED.value, 0),
            _Unit.COUNT,
        ),
    ]
    lines += _table(["指标", name_a, name_b, "差"], cost_rows)
    lines.append("")
    return lines


def _cost_row(
    label: str,
    value_a: float | None,
    value_b: float | None,
    unit: _Unit,
) -> list[str]:
    """成本表的一行 (单位跟着值走, 免得差那一列说不出量纲)."""
    left = _Row(label, _render(value_a, unit), value_a, unit)
    right = _Row(label, _render(value_b, unit), value_b, unit)
    return [label, left.text, right.text, _delta(left, right)]


# --- 显示与差值: 一处定义, 两处用 ---------------------------------------------
#
# 同一个量在两个地方要写: 显示它, 以及写它与另一组/另一跑的差. 这两处**必须同口径**
# ——不然「平均耗时 1.234s」旁边跟一个「+122.0」谁都不知道那是毫秒还是秒. 于是
# 单位种类把它俩绑在一起 (`_Style`), 而不是各写各的格式化串.


class _Unit(StrEnum):
    """一个量的种类 (决定显示与差值怎么量)."""

    COUNT = "count"  # 计数 (跑次 / 截断次数)
    NUMBER = "number"  # 一般数值 (平均轮数 / 平均 token)
    RATIO = "ratio"  # 比率 (0.667 显示成 66.7%)
    SECONDS = "seconds"  # 毫秒级的耗时 (值以毫秒给, 显示成秒)
    MONEY = "money"  # 金额 (元, 与 runs.total_cost 同标度)


@dataclass(frozen=True, slots=True)
class _Style:
    """一种量的写法.

    attributes:
        digits: 显示几位小数.
        scale: 显示前的倍数 (毫秒转秒是 0.001, 比率转百分比是 100).
        prefix / suffix: 值两端的符号.
        delta_suffix: **差**那一列的后缀; 与 `suffix` 不同的就它一个 —— 比率行的差
            说的是**百分点** (66.7% 与 75.0% 差 8.3 个百分点), 不是「相对涨了
            12.5%」, 那是两种完全不同的读法.
    """

    digits: int
    scale: float = 1.0
    prefix: str = ""
    suffix: str = ""
    delta_suffix: str = ""


_STYLES: dict[_Unit, _Style] = {
    _Unit.COUNT: _Style(digits=0),
    _Unit.NUMBER: _Style(digits=1),
    _Unit.RATIO: _Style(digits=1, scale=100, suffix="%", delta_suffix=" 个百分点"),
    _Unit.SECONDS: _Style(digits=3, scale=0.001, suffix="s"),
    # 6 位小数与 `runs.total_cost` 那一列同标度
    _Unit.MONEY: _Style(digits=6, prefix="¥"),
}


def _render(value: float | None, unit: _Unit) -> str:
    """一个值 → 显示文本; None 给占位符 (不是 0 —— 「没数」与「是零」两回事)."""
    if value is None:
        return DASH
    style = _STYLES[unit]
    return f"{style.prefix}{value * style.scale:.{style.digits}f}{style.suffix}"


def _render_delta(diff: float, unit: _Unit, *, signed: bool) -> str:
    """一个差值 → 显示文本 (口径与 `_render` 同源).

    先按该单位的位数格式化再看是不是零 —— 否则 `0.0004s` 会印成 `-0.000s`,
    一个带着负号的零 (那是排版噪声, 不是信息).
    """
    style = _STYLES[unit]
    scaled = diff * style.scale
    if round(scaled, style.digits) == 0:
        return "0"
    text = f"{scaled:.{style.digits}f}{style.delta_suffix}"
    return f"+{text}" if signed and scaled > 0 else text


# --- 小工具 ------------------------------------------------------------------


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """一张 Markdown 表 (表头 + 分隔行 + 数据行)."""
    lines = ["| " + " | ".join(str(item) for item in header) + " |"]
    lines.append("|" + "---|" * len(header))
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return lines


def _cell(value: Any) -> str:
    """一格的值 → 文本: 竖线转义 (不然表格散架), 太长截断, None 给占位符."""
    if value is None:
        return DASH
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool | int | float):
        text = str(value)
    else:
        text = json.dumps(value, ensure_ascii=False)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= _CELL_LIMIT else text[: _CELL_LIMIT - 1] + "…"


def _delta(earlier: _Row, later: _Row) -> str:
    """两行的差: `later - earlier` —— 表里读作「从前面那组到后面那组变了多少」.

    方向定死成「后减前」而不是「前减后」: 表是从左往右读的, 而这一列回答的是
    「改完之后变了多少」. 有一个没数就给占位符.

    正负号一定写出来: 「差 12.3」看不出谁好谁坏, 而这张表的读者正是来问这个的.
    """
    if earlier.value is None or later.value is None:
        return DASH
    return _render_delta(later.value - earlier.value, later.unit, signed=True)


def _spread(rows: Sequence[_Row]) -> str:
    """极差 (最大减最小); 能算的少于两个就给占位符."""
    known = [row.value for row in rows if row.value is not None]
    if len(known) < 2:
        return DASH
    return _render_delta(max(known) - min(known), rows[0].unit, signed=False)
