"""eval 报告与对照 (#58: 报告要能直接读, diff 要能看出波动).

两块: **汇总口径** (池化而不是比率平均、截断不进分母、没分母单列) 与 **排版**
(七块齐全、对照表带波动、配置快照拍平). 外加 `compare` 那条只读文件的路 ——
它读的是报告自己落的那份 JSON, 于是「文件里读出来的」与「报告里印出来的」必须
是同一件事.

载体重用 `test_eval_runner.py` 那套极小业务 (两组 x 五题), 不另造一份 —— 报告
那几块要真的有东西可排才验得动.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_eval_runner import (
    CASES,
    FIXED_NOW,
    _group,
    _runner,
)

from CharAgent.eval import (
    DASH,
    Attempt,
    EvalCase,
    EvalConfigError,
    EvalGroup,
    EvalRunner,
    Judgment,
    Metric,
    RunFacts,
    RunOutcome,
    compare_files,
    compare_reports,
    load_report,
    pick_group,
    render_comparison,
    summarize_attempts,
)
from CharAgent.eval import __main__ as cli


async def _report(*, times: int = 3, two_groups: bool = True) -> Any:
    """跑一份载体报告 (两组或一组)."""
    groups = (
        [_group("全挂"), _group("裁剪", pruned=True)]
        if two_groups
        else [_group("全挂")]
    )
    return await _runner().run(groups, times=times, now=FIXED_NOW)


# ---------------------------------------------------------------------------
# 汇总口径
# ---------------------------------------------------------------------------


def _attempt(
    case_id: str,
    index: int,
    *,
    outcome: RunOutcome = RunOutcome.COMPLETED,
    judgments: dict[str, Judgment] | None = None,
) -> Attempt:
    """手搓一条跑次 (汇总口径那几条要精确控制分子分母)."""
    case = EvalCase(id=case_id, question="题")
    return Attempt(
        case=case,
        index=index,
        facts=RunFacts(outcome=outcome),
        judgments=judgments or {},
    )


def test_the_pooled_rate_adds_numerators_and_denominators_separately() -> None:
    """池化: 分子分母分别相加再相除, **不是**各个比率求平均.

    两道题: 一道 1/1, 一道 1/3 —— 池化是 2/4 = 50%, 比率平均是 (100% + 33%) / 2
    = 67%, 后者会让「只该调 1 个工具」的题与「该调 3 个工具」的题权重相同.
    """
    judge = Judgment(ok=True, metrics={"召回率": Metric(1, 1)})
    heavy = Judgment(ok=True, metrics={"召回率": Metric(1, 3)})
    summary = summarize_attempts(
        [
            _attempt("a", 1, judgments={"选": judge}),
            _attempt("b", 1, judgments={"选": heavy}),
        ],
        ["选"],
    )
    metric = summary["judges"]["选"]["metrics"]["召回率"]

    assert metric["hit"] == 2
    assert metric["total"] == 4
    assert metric["rate"] == 0.5


def test_truncated_runs_never_reach_the_denominator() -> None:
    """截断 / 挂起 / 坏了三种跑次都不进判据的分母 (但如实进分布)."""
    judged = Judgment(ok=False, metrics={"召回率": Metric(0, 1)})
    attempts = [
        _attempt("a", 1, judgments={"选": judged}),
        _attempt("a", 2, outcome=RunOutcome.TRUNCATED, judgments={"选": judged}),
        _attempt("a", 3, outcome=RunOutcome.SUSPENDED, judgments={"选": judged}),
        _attempt("a", 4, outcome=RunOutcome.BROKEN, judgments={"选": judged}),
    ]
    summary = summarize_attempts(attempts, ["选"])

    assert summary["attempts"] == 4
    assert summary["counted"] == 1
    assert summary["outcomes"] == {
        "completed": 1,
        "truncated": 1,
        "suspended": 1,
        "broken": 1,
    }
    assert summary["judges"]["选"]["total"] == 1
    assert summary["judges"]["选"]["metrics"]["召回率"]["total"] == 1


def test_a_metric_without_a_denominator_is_counted_separately() -> None:
    """零期望样本 (分母为 0) 单列计数, 不进池、也不算 0 分."""
    zero = Judgment(ok=True, metrics={"召回率": Metric(0, 0)})
    real = Judgment(ok=True, metrics={"召回率": Metric(1, 1)})
    summary = summarize_attempts(
        [
            _attempt("a", 1, judgments={"选": zero}),
            _attempt("b", 1, judgments={"选": real}),
        ],
        ["选"],
    )
    metric = summary["judges"]["选"]["metrics"]["召回率"]

    assert metric["no_denominator"] == 1
    assert metric["total"] == 1
    assert metric["rate"] == 1.0
    assert summary["judges"]["选"]["ok"] == 2  # 零期望那道题照样算过


def test_a_judge_that_did_not_run_neither_passes_nor_fails() -> None:
    """判据抛错的那一跑在这条判据上既不进分子也不进分母 (与判为不过分开)."""
    passed = Judgment(ok=True)
    failed = Judgment(ok=False)
    attempts = [
        _attempt("a", 1, judgments={"选": passed, "答": passed}),
        _attempt("a", 2, judgments={"选": failed, "答": passed}),
        # 第 3 跑里「答」这个判据抛了错: 它既不是过也不是不过
        Attempt(
            case=EvalCase(id="a", question="题"),
            index=3,
            facts=RunFacts(),
            judgments={"选": failed},
            judge_errors={"答": "ValueError: 抄错了"},
        ),
    ]
    summary = summarize_attempts(attempts, ["选", "答"])

    assert summary["judges"]["选"] == {"ok": 1, "total": 3, "errors": 0, "metrics": {}}
    assert summary["judges"]["答"]["total"] == 2
    assert summary["judges"]["答"]["errors"] == 1
    assert summary["judge_errors"] == 1


def test_the_cost_and_size_averages_count_every_attempt() -> None:
    """成本规模那几行数的是**全部**跑次 (截断的那几次一样花了钱)."""
    ran = Attempt(
        case=EvalCase(id="a", question="题"),
        index=1,
        facts=RunFacts(turns=4, tokens=100, elapsed_ms=2000.0, cost=0.5),
    )
    cut = Attempt(
        case=EvalCase(id="b", question="题"),
        index=1,
        facts=RunFacts(
            outcome=RunOutcome.TRUNCATED, turns=2, tokens=50, elapsed_ms=1000.0
        ),
    )
    summary = summarize_attempts([ran, cut], [])

    assert summary["avg_turns"] == 3.0
    assert summary["avg_tokens"] == 75.0
    assert summary["avg_elapsed_ms"] == 1500.0
    # 只有一次报了金额: 总金额是它, 而不是「按两次摊」
    assert summary["total_cost"] == 0.5


def test_no_cost_at_all_is_not_zero() -> None:
    """一跑都没算过金额时给 None (报告里印 `—`), 不是 0 —— 两者是两回事."""
    summary = summarize_attempts([_attempt("a", 1)], [])
    assert summary["total_cost"] is None
    assert summarize_attempts([], [])["avg_turns"] is None


# ---------------------------------------------------------------------------
# 七块
# ---------------------------------------------------------------------------


async def test_the_report_has_the_seven_blocks() -> None:
    """七块齐全, 顺序对 (票据验收第 2 条)."""
    text = (await _report()).to_markdown()
    positions = [
        text.index("## 1. 配置快照"),
        text.index("## 2. 对照总表"),
        text.index("## 3. 波动"),
        text.index("## 4. 逐题表"),
        text.index("## 5. 失败样本"),
        text.index("## 6. 差异归因"),
        text.index("## 7. 两类收益"),
    ]
    assert positions == sorted(positions)


async def test_a_single_group_report_says_why_blocks_six_and_seven_are_empty() -> None:
    """一组时第 6/7 块仍占位 (并说清为什么空) —— 七块的骨架不因少一组就缺角."""
    text = (await _report(two_groups=False)).to_markdown()
    assert "## 2. 本组总表" in text
    assert "## 6. 差异归因" in text and "只有一组" in text
    assert "## 7. 两类收益" in text


async def test_the_config_snapshot_flattens_nested_knobs() -> None:
    """配置快照把嵌套那几层拍平 (刹车.max_turns 这种写法), 两组并排一行一个键."""
    text = (await _report()).to_markdown()
    for key in (
        "| 模型 |",
        "| 工具数 |",
        "| 刹车.max_turns |",
        "| 压缩.工具结果截断 |",
    ):
        assert key in text
    assert '| 工具集 | ["add", "shout"] | ["add"] |' in text


async def test_a_batch_with_two_config_snapshots_is_flagged() -> None:
    """同一组里出现两份不同的配置快照要**点名** (悄悄用第一份糊过去等于撒谎)."""
    base = _group("全挂")
    seen = {"count": 0}

    async def build(case: EvalCase) -> Any:
        """第 2 个对象交出另一份配置 (模拟「这一批中途换了参数」)."""
        inner = await base.build_subject(case)
        seen["count"] += 1
        stamp = seen["count"]
        return _Reconfigured(inner, stamp)

    group = EvalGroup(name="全挂", build_subject=build, cases=CASES[:1])
    report = await _runner().run([group], times=2, now=FIXED_NOW)
    data = report.to_dict()["groups"][0]

    assert len(data["config_conflicts"]) == 2
    assert data["config"]["模型"] == "mock-llm"
    assert "出现了 2 份不同的配置快照" in report.to_markdown()


class _Reconfigured:
    """把里层的事实换一份配置交出去 (只有配置不同, 其余照搬)."""

    def __init__(self, inner: Any, stamp: int) -> None:
        self._inner = inner
        self._stamp = stamp

    async def run_once(self, case: EvalCase) -> RunFacts:
        """转发, 但把配置改一个数."""
        facts = await self._inner.run_once(case)
        return replace(facts, config={**facts.config, "批次": self._stamp})

    async def aclose(self) -> None:
        """转发."""
        await self._inner.aclose()


async def test_the_case_table_lists_one_row_per_attempt() -> None:
    """逐题表按**跑次**摊平 (五题 x 三次 = 十五行), 题号带 `#第几跑`."""
    text = (await _report(two_groups=False)).to_markdown()
    block = text[text.index("## 4. 逐题表") : text.index("## 5. 失败样本")]
    for case in CASES:
        for index in (1, 2, 3):
            assert f"| {case.id} #{index} |" in block


async def test_the_badcase_block_stops_at_finished_but_failed_runs() -> None:
    """第 5 块只列**跑完了但没判过**的跑次; 没跑完的另给一句小结 (不是答错)."""
    text = (await _report(two_groups=False)).to_markdown()
    block = text[text.index("## 5. 失败样本") : text.index("## 6. 差异归因")]

    assert "| q3 #1 |" in block  # 跑完了但没判过
    assert "| q5 #1 |" not in block  # 截断的那三跑不在这里
    assert "没跑完的跑次: 截断 3" in block


async def test_the_fluctuation_block_gives_each_run_and_a_spread() -> None:
    """波动那一块: 每一跑各自的数 + 极差 (跑 3 次的正当性)."""
    text = (await _report(two_groups=False)).to_markdown()
    block = text[text.index("## 3. 波动") : text.index("## 4. 逐题表")]

    assert "| 指标 | 第 1 跑 | 第 2 跑 | 第 3 跑 | 极差 |" in block
    assert "(每题跑 3 次. 极差 = 最大减最小, 只对能算的指标算)" in block


async def test_the_attribution_block_carries_both_directions() -> None:
    """差异归因带**两边各自的通过率**, 且两个方向都列出来 (不是单向碾压)."""
    text = (await _report()).to_markdown()
    block = text[text.index("## 6. 差异归因") : text.index("## 7. 两类收益")]

    assert "| 题号 | 全挂 通过率 | 裁剪 通过率 | 差 |" in block
    # 差是「后一列减前一列」: 裁剪组 (第二列) 在这道题上掉了 100 个百分点
    assert "| q2 | 100.0% | 0.0% | -100.0 个百分点 |" in block
    assert "| q3 | 0.0% | 100.0% | +100.0 个百分点 |" in block
    assert "全挂 更好的题 (1): q2" in block
    assert "裁剪 更好的题 (1): q3" in block


async def test_a_rate_row_delta_says_percentage_points() -> None:
    """比率行的差写**百分点**并带单位, 免得读成「相对涨了多少」."""
    text = (await _report()).to_markdown()
    assert "个百分点" in text
    assert "| 工具选择 通过 | 9/12 (75.0%) | 9/12 (75.0%) | 0 |" in text


async def test_the_gains_block_keeps_accuracy_and_cost_apart() -> None:
    """两类收益分开说, 且各自的表头写明分母口径 (一个只数跑完的, 一个数全部)."""
    text = (await _report()).to_markdown()
    block = text[text.index("## 7. 两类收益") :]

    assert "### 准确率收益 (只在跑完的样本内比)" in block
    assert "### 成本收益 (数的是全部跑次, 含没跑完的)" in block
    assert "| 截断跑次 |" in block


# ---------------------------------------------------------------------------
# JSON 与 compare
# ---------------------------------------------------------------------------


async def test_the_json_carries_every_attempt_and_the_same_numbers() -> None:
    """JSON 里逐条有跑次 (程序 diff 得动), 且与 Markdown 同源 (一份数据两处排版)."""
    report = await _report(two_groups=False)
    data = json.loads(report.to_json())
    group = data["groups"][0]

    assert data["schema_version"] == 1
    assert data["times"] == 3
    assert group["summary"]["attempts"] == len(CASES) * 3
    first = group["cases"][0]["attempts"][0]
    assert first["tool_calls"][0]["tool_name"] == "add"
    assert first["run_id"] == "run-q1"
    assert first["cost"] == pytest.approx(0.000123)


async def test_compare_reads_two_files_and_writes_a_table(tmp_path: Path) -> None:
    """`compare` 走**落盘的文件** (票据验收第 4 条): 两份 JSON 进, 差异表出."""
    left = await _report(two_groups=False)
    right = await _report(two_groups=False, times=3)
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    path_a.write_text(left.to_json(), encoding="utf-8")
    path_b.write_text(right.to_json(), encoding="utf-8")

    text = compare_files(str(path_a), str(path_b))

    assert text.startswith("# 对照: 全挂 vs 全挂")
    assert "## 2. 对照总表" in text
    assert "## 6. 差异归因" in text
    assert "## 7. 两类收益" in text
    # 同一批跑两次 → 每一行都一致, 差异归因那块如实说「分不出高下」
    assert "通过率两边完全一致" in text
    assert load_report(str(path_a))["times"] == 3


async def test_the_comparison_shows_the_spread_so_noise_is_not_read_as_progress() -> (
    None
):
    """对照表必须能显示波动 —— 否则读者会把两次跑分之间的噪声当成改进.

    三样都要有: 两组的差值 (总表), 各自每一跑的数与极差 (波动), 以及逐题的通过率
    (差异归因) —— 少了波动那一样, 一个 +25 个百分点的差到底是不是噪声就没法判断.
    """
    a = _group_payload("改前", rate=0.5)
    b = _group_payload("改后", rate=0.75)
    text = render_comparison(a, b)

    assert "| 工具选择·召回率 | 50.0% | 75.0% | +25.0 个百分点 |" in text
    assert "## 3. 波动" in text and "| 极差 |" in text
    assert "改后 更好的题 (1): q1" in text
    assert "改前 更好的题 (0): (无)" in text


async def test_the_comparison_says_which_config_each_side_is() -> None:
    """对照那一页也要摆配置快照 —— 不写下来, 隔一周就说不清那个差是哪两版之间的事."""
    text = render_comparison(
        _group_payload("改前", rate=0.5), _group_payload("改后", rate=0.75)
    )

    assert "## 1. 配置快照" in text
    assert "| 配置项 | 改前 | 改后 |" in text
    assert "| 模型 | mock | mock |" in text


def _group_payload(name: str, *, rate: float) -> dict[str, Any]:
    """手搓一份规范形状的**一组** (对照那两块只要这点东西就够验)."""
    summary = {
        "attempts": 3,
        "counted": 3,
        "outcomes": {"completed": 3, "truncated": 0, "suspended": 0, "broken": 0},
        "zero_call": 0,
        "judge_errors": 0,
        "avg_turns": 2.0,
        "avg_tokens": 100.0,
        "avg_elapsed_ms": 1000.0,
        "total_cost": 0.001,
        "judges": {
            "工具选择": {
                "ok": round(rate * 3),
                "total": 3,
                "errors": 0,
                "metrics": {
                    "召回率": {
                        "hit": rate * 3,
                        "total": 3.0,
                        "rate": rate,
                        "no_denominator": 0,
                    }
                },
            }
        },
    }
    return {
        "name": name,
        "judges": ["工具选择"],
        "config": {"模型": "mock"},
        "summary": summary,
        "fluctuation": [summary],
        "cases": [
            {
                "id": "q1",
                "question": "题",
                "expect_tools": ["add"],
                "expect_args": {},
                "meta": {},
                "attempts": [],
                "rolled": {
                    "attempts": 3,
                    "counted": 3,
                    "passed": round(rate * 3),
                    "rate": rate,
                    "outcomes": {},
                },
            }
        ],
        "badcases": [],
    }


def test_compare_reports_takes_already_loaded_data() -> None:
    """给已经拿在手上的调用方留的路 (不必先落盘再读回来)."""
    data = json.loads(_json_fixture())
    text = compare_reports(data, data)
    assert "通过率两边完全一致" in text


def _json_fixture() -> str:
    """一份最小的规范形状报告 (两份一样的, 用来验「读得进来」)."""
    group = _group_payload("唯一", rate=1.0)
    return json.dumps(
        {
            "schema_version": 1,
            "generated_at": FIXED_NOW.isoformat(),
            "times": 3,
            "judges": ["工具选择"],
            "groups": [group],
        },
        ensure_ascii=False,
    )


async def test_picking_a_group_needs_a_name_when_the_file_has_two() -> None:
    """一份报告含两组时, 得说清比哪一组 (含糊地拿第一组去比, 比出来的东西说不清)."""
    data = (await _report()).to_dict()

    with pytest.raises(EvalConfigError) as info:
        pick_group(data)
    assert "得说清比哪一组" in str(info.value)
    assert pick_group(data, "裁剪")["name"] == "裁剪"

    with pytest.raises(EvalConfigError) as info:
        pick_group(data, "不存在的组")
    assert "没有 '不存在的组' 这一组" in str(info.value)


async def test_compare_can_pick_a_group_from_each_file(tmp_path: Path) -> None:
    """一份报告含两组时, 也能挑出指定的那两组来比."""
    left = await _report()
    path = tmp_path / "两组的.json"
    path.write_text(left.to_json(), encoding="utf-8")

    text = compare_files(str(path), str(path), group_a="全挂", group_b="裁剪")
    assert text.startswith("# 对照: 全挂 vs 裁剪")


def test_a_report_that_is_not_ours_is_refused(tmp_path: Path) -> None:
    """不是本包的 JSON / 版本对不上 —— 当场拒绝, 不拿两套口径硬比一通."""
    wrong_version = tmp_path / "老版本.json"
    wrong_version.write_text(
        json.dumps({"schema_version": 0, "groups": [{"name": "x"}]}), encoding="utf-8"
    )
    with pytest.raises(EvalConfigError) as info:
        load_report(str(wrong_version))
    assert "schema_version" in str(info.value)

    not_json = tmp_path / "不是.json"
    not_json.write_text("这不是 JSON", encoding="utf-8")
    with pytest.raises(EvalConfigError) as info:
        load_report(str(not_json))
    assert "不是合法 JSON" in str(info.value)

    missing = tmp_path / "没有.json"
    with pytest.raises(EvalConfigError) as info:
        load_report(str(missing))
    assert "读不了这份报告" in str(info.value)


def test_three_groups_cannot_be_printed_or_compared() -> None:
    """三组以上连排版都表达不了 (对照是两两比)."""
    from CharAgent.eval import render_markdown

    data = {
        "schema_version": 1,
        "generated_at": "",
        "times": 1,
        "judges": [],
        "groups": [{"name": "一"}, {"name": "二"}, {"name": "三"}],
    }
    with pytest.raises(EvalConfigError) as info:
        render_markdown(data)
    assert "只表达两两比" in str(info.value)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


async def test_the_command_line_writes_the_table(tmp_path: Path) -> None:
    """`python -m CharAgent.eval compare a.json b.json` —— 打到屏幕或落盘."""
    report = await _report(two_groups=False)
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    path_a.write_text(report.to_json(), encoding="utf-8")
    path_b.write_text(report.to_json(), encoding="utf-8")

    assert cli.main(["compare", str(path_a), str(path_b)]) == 0

    out = tmp_path / "对照.md"
    assert cli.main(["compare", str(path_a), str(path_b), "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# 对照:")


def test_the_command_line_says_what_is_wrong_instead_of_crashing(
    tmp_path: Path,
) -> None:
    """报告读不了时给一句人话 + 非零退出码 (不是一屏 traceback)."""
    assert cli.main(["compare", str(tmp_path / "没这个.json"), "whatever.json"]) == 1


def test_the_dash_is_the_only_placeholder_for_no_number() -> None:
    """没数的占位符只有一种写法 (报告各处一致), 且五种单位都走同一处渲染."""
    from CharAgent.eval.report import _render, _render_delta, _Unit

    for unit in _Unit:
        assert _render(None, unit) == DASH
    assert _render(0.667, _Unit.RATIO) == "66.7%"
    assert _render(2100.0, _Unit.SECONDS) == "2.100s"
    assert _render(0.000123, _Unit.MONEY) == "¥0.000123"
    assert _render(2.0, _Unit.NUMBER) == "2.0"
    assert _render(3, _Unit.COUNT) == "3"
    # 差的口径跟着单位走: 比率说百分点, 耗时说秒
    assert _render_delta(0.083, _Unit.RATIO, signed=True) == "+8.3 个百分点"
    assert _render_delta(0.0004, _Unit.SECONDS, signed=False) == "0"


def test_the_runner_and_the_report_agree_on_the_judge_order() -> None:
    """判据的列序就是装配时给的顺序 (Python 的字典保序, 报告照它排)."""
    runner = EvalRunner(judges={"乙": _Always(), "甲": _Always()})
    assert runner.judges == ("乙", "甲")


class _Always:
    """恒过的判据 (只在验列序时用)."""

    def judge(self, case: EvalCase, facts: RunFacts) -> Judgment:
        """恒过."""
        return Judgment(ok=True)
