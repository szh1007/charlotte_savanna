"""报告渲染: 拿一份合成的 payload 走一遍 JSON + Markdown 两条路.

这条用例的来历: 逐题表把「指标块」(dict) 当数字传给了 `_pct`, 上游计分全绿,
只有渲染层没人测 —— 真跑批时当场 `TypeError: dict * int`. 渲染是报告的最后一米,
它崩了等于跑批白跑, 所以这里把两个出口都钉住.
"""

from __future__ import annotations

import json

from app.eval.golden import GoldenCase
from app.eval.metrics import L1Outcome
from app.eval.report import render_markdown, write_reports
from app.eval.runner import CaseRecord, RunRecord, summarize_run


def _case(case_id: str, *, eex: bool = False, metric: str | None = None) -> GoldenCase:
    return GoldenCase(
        id=case_id,
        category="single_table",
        question="一共有多少笔订单",
        gold_tables=("fact_order",),
        gold_columns=("fact_order.order_id",),
        gold_metric=metric,
        gold_sql="SELECT COUNT(order_id) FROM fact_order",
        eex=eex,
        notes="口径: 全部订单",
    )


def _run(
    index: int,
    *,
    ex: bool = True,
    eex_pass: bool | None = None,
    metric_hit: bool | None = True,
) -> RunRecord:
    record = RunRecord(index=index)
    record.l1 = L1Outcome(
        column_precision=0.5, column_recall=1.0, table_hit=True, metric_hit=metric_hit
    )
    record.l1_at_merge = record.l1
    record.ex_ok = ex
    record.sql_final = "SELECT COUNT(order_id) FROM fact_order"
    record.eex_pass = eex_pass
    return record


def _report() -> dict:
    records = [
        CaseRecord(case=_case("single-01", eex=True), runs=[_run(0, eex_pass=True)]),
        CaseRecord(
            case=_case("single-02", metric="GMV"), runs=[_run(0, metric_hit=False)]
        ),
    ]
    return summarize_run(records, {"run_at": "2026-10-05 23:00", "times": 1})


def test_render_markdown_survives_block_valued_cells() -> None:
    """回归: 逐题表里的「表命中 / 指标命中」是块 (dict), 不是数字."""
    markdown = render_markdown(_report())

    assert "L1 列召回率" in markdown
    assert "100.0%" in markdown, "块里的均值要按百分比渲染出来"
    assert "0%" in markdown, "未命中的指标块要能渲染成 0%"
    assert "single-01" in markdown and "single-02" in markdown


def test_per_case_cell_shows_the_real_range() -> None:
    """回归: 逐题块的极差键是 `range` —— 早先读了不存在的 `avg_range`, 恒显 ±0.00."""
    record = CaseRecord(
        case=_case("single-01"),
        runs=[_run(0), _run(1), _run(2, ex=False)],
    )
    record.runs[2].l1 = L1Outcome(
        column_precision=0.5, column_recall=0.5, table_hit=True, metric_hit=None
    )
    report = summarize_run([record], {"run_at": "2026-10-05 23:00", "times": 3})

    markdown = render_markdown(report)

    assert "(±0.50)" in markdown, "臂内 1.0 与 0.5 的极差要如实显示"


def test_single_run_report_shows_n_a_instead_of_zero_range() -> None:
    """只跑 1 次时极差无从谈起 —— 显示 n/a, 不能显示成 0.0「稳如泰山」."""
    report = summarize_run(
        [CaseRecord(case=_case("single-01"), runs=[_run(0)])],
        {"run_at": "2026-10-05 23:00", "times": 1},
    )

    assert report["summary"]["l1"]["column_recall"]["avg_range"] is None
    assert "n/a" in render_markdown(report)


def test_render_markdown_survives_a_subset_without_metric_questions() -> None:
    """回归: 子集里没有指标题时 `metric_hit_rate` 是 None, 渲染层不能崩.

    这条从 C15 起潜伏 —— 全量跑批总有指标题, 直到 C16 用 `--cases single-01`
    选单题复跑才撞出来 (TypeError: 'NoneType' object is not subscriptable).
    """
    report = summarize_run(
        [CaseRecord(case=_case("single-01"), runs=[_run(0, metric_hit=None)])],
        {"run_at": "2026-10-06 01:30", "times": 1},
    )

    assert report["summary"]["l1"]["metric_hit_rate"] is None
    markdown = render_markdown(report)

    assert "| 指标命中率 | n/a | n/a |" in markdown
    assert "| 列召回率 | 100.0%" in markdown


def test_render_markdown_lists_failures_and_standing_notes() -> None:
    record = _run(0, ex=False)
    record.ex_error = "Unknown column 'x'"
    report = summarize_run(
        [CaseRecord(case=_case("single-01"), runs=[record])],
        {"run_at": "2026-10-05 23:00", "times": 1},
    )

    markdown = render_markdown(report)

    assert "失败样本清单" in markdown
    assert "Unknown column 'x'" in markdown
    assert "不做什么与为什么" in markdown
    assert "L3" in markdown, "L3 不做的理由要落在报告里, 不能是沉默的缺席"


def test_write_reports_round_trips_both_formats(tmp_path) -> None:
    report = _report()

    json_path, md_path = write_reports(report, tmp_path, stem="baseline")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["l1"]["column_recall"]["mean"] == 1.0
    assert payload["per_case"][0]["id"] == "single-01"
    assert "rag_text2sql 评估报告" in md_path.read_text(encoding="utf-8")
