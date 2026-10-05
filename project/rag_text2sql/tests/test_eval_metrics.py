"""L1 / EEX / 极差: 判据都是纯函数, 不碰真服务.

口径先说清 (与报告头部的写法一致):
- 列精确率 / 召回率: **逐题算再取平均** (macro), 不是把全部列混在一起算 micro ——
  30~50 题的规模上, micro 会被列多的题主导.
- 候选集取自**节点输出** (filter_table / filter_metric 之后), 不是最终 SQL 的文本.
- 表必命中率: gold 表集合 **全部** 出现在候选表里才算命中 (查全, 不是查准).
- EEX: 结果集比较忽略列序与行序, 数值保留两位小数 (浮点噪声不参与判定).
"""

from __future__ import annotations

import pytest

from app.eval.metrics import (
    Spread,
    normalize_rows,
    results_equal,
    score_l1,
    spread,
)

# ---------------------------------------------------------------------------
# L1
# ---------------------------------------------------------------------------


def test_perfect_retrieval_scores_one() -> None:
    outcome = score_l1(
        gold_tables={"fact_order", "dim_region"},
        gold_columns={"fact_order.order_amount", "fact_order.region_id"},
        gold_metric="GMV",
        retrieved_tables={"fact_order", "dim_region"},
        retrieved_columns={
            "fact_order.order_amount",
            "fact_order.region_id",
            "fact_order.order_id",  # 多带的主键在此计入噪声 (精确率会被拉低)
        },
        retrieved_metrics={"GMV"},
    )

    assert outcome.column_recall == 1.0
    assert outcome.column_precision == pytest.approx(2 / 3)
    assert outcome.table_hit is True
    assert outcome.metric_hit is True


def test_missing_gold_column_costs_recall() -> None:
    outcome = score_l1(
        gold_tables={"fact_order"},
        gold_columns={"fact_order.order_amount", "fact_order.order_quantity"},
        gold_metric=None,
        retrieved_tables={"fact_order"},
        retrieved_columns={"fact_order.order_amount"},
        retrieved_metrics=set(),
    )

    assert outcome.column_recall == 0.5
    assert outcome.column_precision == 1.0
    assert outcome.metric_hit is None, "无指标的题不参与指标命中率"


def test_missing_one_gold_table_fails_the_must_hit() -> None:
    outcome = score_l1(
        gold_tables={"fact_order", "dim_region"},
        gold_columns={"fact_order.order_amount"},
        gold_metric=None,
        retrieved_tables={"fact_order"},
        retrieved_columns={"fact_order.order_amount"},
        retrieved_metrics=set(),
    )

    assert outcome.table_hit is False, "必命中 = 全中, 缺一张表就是没命中"


def test_empty_candidate_set_scores_zero_not_nan() -> None:
    outcome = score_l1(
        gold_tables={"fact_order"},
        gold_columns={"fact_order.order_amount"},
        gold_metric="GMV",
        retrieved_tables=set(),
        retrieved_columns=set(),
        retrieved_metrics=set(),
    )

    assert outcome.column_recall == 0.0
    assert outcome.column_precision == 0.0
    assert outcome.table_hit is False
    assert outcome.metric_hit is False


def test_wrong_metric_fails_the_metric_hit() -> None:
    outcome = score_l1(
        gold_tables={"fact_order"},
        gold_columns={"fact_order.order_amount"},
        gold_metric="GMV",
        retrieved_tables={"fact_order"},
        retrieved_columns={"fact_order.order_amount"},
        retrieved_metrics={"AOV"},
    )

    assert outcome.metric_hit is False


# ---------------------------------------------------------------------------
# 极差 (臂内波动): 一次跑出来的差不能当结论
# ---------------------------------------------------------------------------


def test_spread_reports_mean_min_max_and_range() -> None:
    result = spread([1.0, 0.5, 0.5])

    assert result.mean == pytest.approx(2 / 3)
    assert result.min == 0.5
    assert result.max == 1.0
    assert result.rng == pytest.approx(0.5)


def test_spread_of_one_value_has_zero_range() -> None:
    result = spread([0.25])

    assert result == Spread(mean=0.25, min=0.25, max=0.25)


def test_spread_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        spread([])


# ---------------------------------------------------------------------------
# EEX: 结果集比较
# ---------------------------------------------------------------------------


def test_row_normalization_ignores_column_and_row_order() -> None:
    left = [{"region": "华北", "total": 100.0}, {"region": "华东", "total": 50.0}]
    right = [{"total": 50.004, "region": "华东"}, {"total": 99.996, "region": "华北"}]

    assert results_equal(left, right)


def test_row_normalization_treats_none_as_a_value() -> None:
    assert results_equal([{"v": None}], [{"v": None}])
    assert not results_equal([{"v": None}], [{"v": 0}])
    assert normalize_rows([{"v": None}]) == [("NULL",)]


def test_different_row_counts_are_not_equal() -> None:
    assert not results_equal([{"v": 1}, {"v": 1}], [{"v": 1}])
