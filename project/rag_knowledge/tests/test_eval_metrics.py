"""`app/rag_eval/metrics.py` 的纯函数.

它们此前**一条用例都没有**, 而报告里那些 P / R / MRR / NDCG 全靠它们算 ——
自己写错一个公式, 报告的结论就是错的, 而且看不出来.
"""

from __future__ import annotations

from app.rag_eval.metrics import (
    compute_chunk_metrics,
    compute_item_name_hit_rate,
    extract_chunk_ids,
    mean_must_hit_rate,
)

# ---------------------------------------------------------------------------
# extract_chunk_ids
# ---------------------------------------------------------------------------


def test_extract_chunk_ids_reads_the_id_field():
    assert extract_chunk_ids([{"chunk_id": "7"}, {"chunk_id": "8"}]) == ["7", "8"]


def test_extract_chunk_ids_normalizes_types_and_dedupes():
    """id 可能是 int 也可能是 str, 统一成字符串后**去重** (顺序本身有含义, 故保序)."""
    assert extract_chunk_ids([{"chunk_id": 7}, {"chunk_id": "7"}, {"chunk_id": 8}]) == [
        "7",
        "8",
    ]


def test_extract_chunk_ids_skips_none_and_keeps_order():
    assert extract_chunk_ids(
        [{"chunk_id": "b"}, {"chunk_id": None}, {"chunk_id": "a"}]
    ) == ["b", "a"]


def test_extract_chunk_ids_tolerates_none():
    assert extract_chunk_ids(None) == []


# ---------------------------------------------------------------------------
# compute_item_name_hit_rate
# ---------------------------------------------------------------------------


def test_item_name_hit_rate_is_intersection_over_expected():
    assert compute_item_name_hit_rate(["A", "B"], ["A", "C"]) == 0.5


def test_item_name_hit_rate_is_zero_when_nothing_matched():
    assert compute_item_name_hit_rate([], ["A"]) == 0.0


def test_item_name_hit_rate_is_not_applicable_without_expectation():
    """没标预期主体的题记 None (不适用), 不是 0, 也不是 1.

    那类题是「该答不知道」—— **正确行为就是认不出主体**, 记 0 等于把答对算成答错;
    记 1 又等于白送分. 汇总是按"适用"平均的, None 直接排除出分母.
    """
    assert compute_item_name_hit_rate(["A"], []) is None
    assert compute_item_name_hit_rate([], []) is None


# ---------------------------------------------------------------------------
# compute_chunk_metrics
# ---------------------------------------------------------------------------


def test_chunk_metrics_on_a_perfect_hit():
    metrics = compute_chunk_metrics(
        retrieved_chunk_ids=["1", "2", "3"],
        gold_chunk_ids=["1", "2", "3"],
        must_hit_chunk_ids=["1"],
    )

    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["must_hit_rate"] == 1.0
    assert metrics["mrr_at_k"] == 1.0


def test_chunk_metrics_penalises_a_late_first_hit():
    """gold 排在第 3 位: MRR 就该是 1/3, 不是 1."""
    metrics = compute_chunk_metrics(
        retrieved_chunk_ids=["x", "y", "1"],
        gold_chunk_ids=["1"],
        must_hit_chunk_ids=["1"],
    )

    assert metrics["mrr_at_k"] == round(1 / 3, 4)


def test_chunk_metrics_reports_recall_loss_when_must_hit_is_missed():
    """必命中没捞到 → 必命中率 0, 但召回率只看交集."""
    metrics = compute_chunk_metrics(
        retrieved_chunk_ids=["2"],
        gold_chunk_ids=["1", "2"],
        must_hit_chunk_ids=["1"],
    )

    assert metrics["must_hit_rate"] == 0.0
    assert metrics["recall"] == 0.5


def test_chunk_metrics_with_nothing_retrieved():
    metrics = compute_chunk_metrics(
        retrieved_chunk_ids=[], gold_chunk_ids=["1"], must_hit_chunk_ids=["1"]
    )

    assert metrics["recall"] == 0.0


def test_chunk_metrics_ndcg_rewards_putting_the_right_answer_first():
    """NDCG@5 的用处就在这里: 同样「都召回了」, 顺序好坏要能分开."""
    good = compute_chunk_metrics(["1", "2", "x"], ["1", "2"], ["1"])["ndcg_at_k"]
    bad = compute_chunk_metrics(["x", "1", "2"], ["1", "2"], ["1"])["ndcg_at_k"]

    assert good > bad


# ---------------------------------------------------------------------------
# must_hit 的口径 (C17 改: 从"覆盖率"改成"最低门槛")
# ---------------------------------------------------------------------------


def test_must_hit_rate_is_a_threshold_not_a_coverage():
    """标了 3 条关键 chunk, 捞到其中 1 条就算过.

    它量的是「最关键的那类内容有没有捞到」, 不是「捞到了几成」—— 后者会随标注条数
    变化, 同一条链路标 1 条时是 1.0、标 3 条时是 0.33, 数字不可比.
    """
    metrics = compute_chunk_metrics(
        retrieved_chunk_ids=["1", "x"],
        gold_chunk_ids=["1", "2", "3"],
        must_hit_chunk_ids=["1", "2", "3"],
    )

    assert metrics["must_hit_rate"] == 1.0
    # 明细照留: 报告里仍然看得到"打中了几条"
    assert metrics["must_hit_count"] == 1


def test_must_hit_rate_is_zero_when_no_key_chunk_is_hit():
    metrics = compute_chunk_metrics(
        retrieved_chunk_ids=["x"],
        gold_chunk_ids=["1", "2"],
        must_hit_chunk_ids=["1", "2"],
    )

    assert metrics["must_hit_rate"] == 0.0


def test_must_hit_rate_is_not_applicable_without_annotation():
    """0 条标注的题 (「该答不知道」那类) 既不算"打中"也不算"打漏".

    记 None: 汇总时被排除出分母 —— 否则要么凭空送分, 要么凭空扣分.
    """
    metrics = compute_chunk_metrics(
        retrieved_chunk_ids=["1"],
        gold_chunk_ids=["1"],
        must_hit_chunk_ids=[],
    )

    assert metrics["must_hit_rate"] is None


def test_mean_must_hit_rate_skips_not_applicable_cases():
    """平均值只在"适用"的题上算."""
    assert mean_must_hit_rate([1.0, 0.0, None]) == 0.5


def test_mean_must_hit_rate_is_none_when_nothing_applies():
    assert mean_must_hit_rate([None, None]) is None


def test_mean_must_hit_rate_of_empty_input_is_none():
    assert mean_must_hit_rate([]) is None
