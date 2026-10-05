"""跑批器的可离网部分: 快照吸收 / 计分汇总.

最要紧的一条是**深拷贝**: `filter_table` 原地删 `table_infos` 里的元素再返回同一个
列表对象 —— 不拷贝的话, merge 阶段的快照会被过滤步骤改掉, 报告里
「召回@merge」永远等于「过滤后」, 那条用来区分「召回丢」与「过滤误删」的诊断行
就整个失效 (而且失效得无声无息).
"""

from __future__ import annotations

import pytest

from app.eval.golden import GoldenCase
from app.eval.metrics import L1Outcome
from app.eval.runner import (
    CandidateSet,
    CaseRecord,
    EvalRunner,
    RunRecord,
    candidate_from,
    merge_records,
    records_from_report,
    summarize_case,
    summarize_run,
)


def _case(**overrides) -> GoldenCase:
    base = {
        "id": "single-01",
        "category": "single_table",
        "question": "一共有多少笔订单",
        "gold_tables": ("fact_order",),
        "gold_columns": ("fact_order.order_id",),
        "gold_metric": None,
        "gold_sql": "SELECT COUNT(order_id) FROM fact_order",
        "eex": False,
        "notes": "口径: 全部订单",
    }
    base.update(overrides)
    return GoldenCase(**base)


def _table_info(name: str, columns: list[str]) -> dict:
    return {
        "name": name,
        "role": "fact",
        "description": f"{name} 的描述",
        "columns": [{"name": column} for column in columns],
    }


# ---------------------------------------------------------------------------
# 候选集
# ---------------------------------------------------------------------------


def test_candidate_set_dedupes_and_prefixes_columns() -> None:
    candidates = candidate_from(
        [
            _table_info("fact_order", ["order_amount", "region_id"]),
            _table_info("fact_order", ["order_amount"]),  # 重复表/列来自多路召回
        ],
        [{"name": "GMV"}, {"name": "GMV"}],
    )

    assert candidates.tables == ("fact_order",)
    assert candidates.columns == ("fact_order.order_amount", "fact_order.region_id")
    assert candidates.metrics == ("GMV",)


# ---------------------------------------------------------------------------
# 快照吸收: 深拷贝那条坑
# ---------------------------------------------------------------------------


def test_merge_snapshot_survives_the_in_place_filtering() -> None:
    table_infos = [_table_info("fact_order", ["order_amount", "order_id"])]
    record = RunRecord(index=0)

    EvalRunner._absorb(
        "merge_retrieve", {"table_infos": table_infos, "metric_infos": []}, record
    )

    # 模拟 filter_table 的原地删改: 它 remove 的是同一个列表里的同一个 dict
    table_infos[0]["columns"].pop()
    EvalRunner._absorb("filter_table", {"table_infos": table_infos}, record)
    EvalRunner._absorb("filter_metric", {"metric_infos": []}, record)

    assert record.merged is not None
    assert record.merged.columns == (
        "fact_order.order_amount",
        "fact_order.order_id",
    ), "merge 快照被抓后又被过滤步骤改掉了 —— 少了一次 deepcopy"
    assert record.filtered is not None
    assert record.filtered.columns == ("fact_order.order_amount",)


def test_execute_sql_success_emits_none_and_must_not_break_absorption() -> None:
    """execute_sql 成功时 update 是 None, 吸收逻辑要容得下 (真机实测的形状)."""
    record = RunRecord(index=0)

    EvalRunner._absorb("execute_sql", {}, record)
    EvalRunner._absorb("correct_sql", {"sql": "SELECT 1", "error": None}, record)

    assert record.sql_final == "SELECT 1"
    assert record.corrected is True


# ---------------------------------------------------------------------------
# 计分汇总
# ---------------------------------------------------------------------------


def _run(index: int, *, recall: float = 1.0, ex: bool = True) -> RunRecord:
    record = RunRecord(index=index)
    record.l1 = L1Outcome(
        column_precision=1.0, column_recall=recall, table_hit=True, metric_hit=None
    )
    record.ex_ok = ex
    return record


def test_summarize_case_reports_mean_and_range_and_counts_crashes() -> None:
    case = _case()
    record = CaseRecord(
        case=case,
        runs=[_run(0, recall=1.0), _run(1, recall=0.5), _run(2, recall=0.5, ex=False)],
    )
    record.runs[2].failed_stage = "graph"

    block = summarize_case(record)

    assert block["column_recall"]["mean"] == 2 / 3
    assert block["column_recall"]["range"] == 0.5, "臂内极差要按题给出"
    assert block["ex_rate"] == 2 / 3, "运行失败没有 SQL, EX 记 0"
    assert block["failed_runs"] == 1


def test_summary_pools_runs_and_lists_failures() -> None:
    case = _case()
    healthy = CaseRecord(case=case, runs=[_run(0), _run(1)])
    broken_run = _run(2, ex=False)
    broken_run.ex_error = "Unknown column 'x'"
    broken_run.sql_final = "SELECT x FROM fact_order"
    broken = CaseRecord(case=_case(id="single-02"), runs=[broken_run])

    report = summarize_run([healthy, broken], {"times": 3})

    assert report["summary"]["l2"]["ex_rate"]["mean"] == 2 / 3
    assert report["summary"]["reliability"]["failed_runs"] == 0, (
        "failed_stage 才是「运行失败」; EX 失败是生成/执行质量问题"
    )
    assert len(report["failures"]) == 1
    assert report["failures"][0]["error"] == "Unknown column 'x'"
    assert len(report["by_category"]) == 1
    assert report["by_category"][0]["ex_rate"] == 2 / 3


def test_metric_hit_block_is_none_when_no_case_has_a_metric() -> None:
    report = summarize_run([CaseRecord(case=_case(), runs=[_run(0)])], {"times": 1})

    assert report["summary"]["l1"]["metric_hit_rate"] is None
    assert report["summary"]["l1"]["column_recall"]["mean"] == 1.0


def test_ex_range_flags_unstable_runs() -> None:
    """EX 也要给极差: 有的 run 通有的不通, 正是「一次跑出来的差不等于结论」的现场."""
    unstable = CaseRecord(
        case=_case(), runs=[_run(0, ex=True), _run(1, ex=True), _run(2, ex=False)]
    )
    stable = CaseRecord(case=_case(id="single-02"), runs=[_run(0), _run(1)])

    report = summarize_run([unstable, stable], {"times": 3})

    assert report["per_case"][0]["ex_range"] == 1.0
    assert report["per_case"][1]["ex_range"] == 0.0
    assert report["summary"]["l2"]["ex_rate"]["avg_range"] == 0.5


def test_ex_splits_no_sql_runs_from_sql_execution_failures() -> None:
    """「没跑完」与「SQL 跑不通」是两类 —— 报告要能分开读, 不能混成一个数."""
    healthy = _run(2)
    healthy.sql_final = "SELECT COUNT(order_id) FROM fact_order"
    timeout_run = _run(0, ex=False)
    timeout_run.failed_stage = "timeout"  # 超时: 根本没产出 SQL
    sql_failure = _run(1, ex=False)
    sql_failure.sql_final = "SELECT x FROM fact_order"  # 有 SQL, 但跑不通
    sql_failure.ex_error = "Unknown column 'x'"

    report = summarize_run(
        [CaseRecord(case=_case(), runs=[timeout_run, sql_failure, healthy])],
        {"times": 3},
    )

    assert report["summary"]["l2"]["ex_rate"]["mean"] == 1 / 3
    assert report["summary"]["l2"]["runs_with_sql"] == 2
    assert report["summary"]["l2"]["ex_rate_with_sql"] == 0.5, (
        "只按产出 SQL 的 run 计: 超时那次剔出去, SQL 跑不通那次留下"
    )


def _scored_run(index: int = 0, case: GoldenCase | None = None) -> RunRecord:
    """一条「像真跑过」的留痕: 候选集 / SQL / EX / EEX 都齐, 派生分也照跑批器算过."""
    case = case or _case()
    run = _run(index)
    run.merged = CandidateSet(
        tables=("fact_order",),
        columns=("fact_order.order_id", "fact_order.order_amount"),
        metrics=(),
    )
    run.filtered_tables = ("fact_order",)
    run.filtered_columns = ("fact_order.order_id",)
    run.filtered_metrics = ()
    run.sql_final = "SELECT COUNT(order_id) FROM fact_order"
    run.ex_ok = True
    run.eex_pass = True
    EvalRunner._score(case, run)
    return run


def test_run_record_round_trips_through_dict() -> None:
    original = _scored_run()

    restored = RunRecord.from_dict(original.to_dict())

    assert restored.merged == original.merged
    assert restored.filtered == original.filtered
    assert restored.sql_final == original.sql_final
    assert restored.ex_ok == original.ex_ok
    assert restored.eex_pass == original.eex_pass
    assert restored.index == original.index


def test_records_from_report_round_trips_the_summary() -> None:
    """并回基线的地基: 报告 -> 记录 -> 报告, 两次的判分必须一模一样.

    这条不成立的话, 「复跑两题并回基线」等于把另外 37 题的分数也悄悄改了.
    """
    case = _case()
    first = summarize_run(
        [CaseRecord(case=case, runs=[_scored_run(0, case), _scored_run(1, case)])],
        {"times": 2},
    )

    records = records_from_report(first, {case.id: case})
    second = summarize_run(records, first["meta"])

    assert first["summary"] == second["summary"]
    assert first["per_case"] == second["per_case"]
    assert first["by_category"] == second["by_category"]


def test_merge_replaces_only_the_rerun_cases() -> None:
    baseline = [
        CaseRecord(case=_case(id="single-01"), runs=[_run(0)]),
        CaseRecord(case=_case(id="single-02"), runs=[_run(0)]),
    ]
    fresh_run = _scored_run(0)
    fresh_run.sql_final = "SELECT COUNT(*) FROM fact_order"
    fresh = [CaseRecord(case=_case(id="single-02"), runs=[fresh_run])]

    merged = merge_records(baseline, fresh)

    assert [record.case.id for record in merged] == ["single-01", "single-02"]
    assert merged[1].runs[0].sql_final == "SELECT COUNT(*) FROM fact_order"
    assert merged[0] is baseline[0], "没复跑的题必须原样保留"


def test_merge_refuses_cases_that_are_not_in_the_baseline() -> None:
    with pytest.raises(KeyError):
        merge_records(
            [CaseRecord(case=_case(id="single-01"), runs=[])],
            [CaseRecord(case=_case(id="join-99"), runs=[])],
        )


def test_records_from_report_refuses_cases_missing_from_the_golden_set() -> None:
    case = _case()
    report = summarize_run([CaseRecord(case=case, runs=[_scored_run()])], {"times": 1})

    with pytest.raises(KeyError):
        records_from_report(report, {})


def test_findings_come_from_the_data_not_from_hardcoded_names() -> None:
    """回归: 上一版的归因与举例是写死的常量 (点名了具体题号与表名) —— 换题库就会失真."""
    case = _case(id="join-99", gold_tables=("fact_order", "dim_x"))
    run = _run(0)
    run.l1 = L1Outcome(
        column_precision=1.0, column_recall=1.0, table_hit=False, metric_hit=None
    )
    run.l1_at_merge = L1Outcome(
        column_precision=1.0, column_recall=1.0, table_hit=True, metric_hit=None
    )
    run.merged = CandidateSet(tables=("fact_order", "dim_x"), columns=(), metrics=())
    run.filtered_tables = ("fact_order",)
    run.filtered_columns = ()
    run.filtered_metrics = ()

    report = summarize_run([CaseRecord(case=case, runs=[run])], {"times": 1})
    text = " ".join(report["findings"])

    assert "dim_x" in text, "缺的表要从本次数据里数出来"
    assert "1/1 次" in text, "次数也要从逐 run 数据里数"
    assert "没有开 EEX" in text, "没有 EEX 覆盖时要如实说未测量, 不能默认等价"
