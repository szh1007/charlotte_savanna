"""耗时汇总 (`app/rag_eval/latency.py`) 的纯函数.

这份报告是 README「延迟拆解」那张表的**唯一来源** —— 解析正则写错一点, 表里
就会少一个节点或者把两次问答的耗时加到一起, 而看表的人无从发现. 所以在这里
拿**日志的真实行格式**钉住 (格式抄自 `shared/runtime/logger.py` 的 `node_log`
与它的 sink 格式, 不是编的).
"""

from __future__ import annotations

from app.rag_eval.latency import (
    build_report,
    parse_node_durations,
    percentile,
    render_markdown,
    summarize,
)

PREFIX = "2026-10-06 03:27:06.024 | INFO     | _11_rerank.py:node_rerank:11 - "


def _line(node: str, trace: str, ms: int) -> str:
    return f"{PREFIX}[{node}] 节点完成, 追踪 ID={trace}, 耗时={ms}ms"


def test_parse_groups_by_trace_id() -> None:
    """按追踪 ID 分组 —— 两次问答的节点耗时不能混在一起."""
    per_trace = parse_node_durations(
        [
            _line("node_search_embedding", "q1", 120),
            _line("node_rerank", "q1", 9000),
            _line("node_search_embedding", "q2", 150),
            _line("node_rerank", "q2", 8800),
        ]
    )

    assert per_trace == {
        "q1": {"node_search_embedding": 120, "node_rerank": 9000},
        "q2": {"node_search_embedding": 150, "node_rerank": 8800},
    }


def test_parse_ignores_other_lines() -> None:
    """别的行 (步骤日志 / 业务日志) 一律不认 —— 认错了会数出假的节点耗时."""
    per_trace = parse_node_durations(
        [
            "[node_rerank] 步骤开始",
            f"{PREFIX}[node_rrf] 节点开始, 追踪 ID=q1",
            "[node_rerank] 节点异常, 追踪 ID=q1",
            _line("node_rerank", "q1", 9000),
        ]
    )

    assert per_trace == {"q1": {"node_rerank": 9000}}


def test_parse_keeps_last_duplicate_node_in_one_trace() -> None:
    """同一追踪 ID 里同名节点出现两次 (重试) 时取最后一次."""
    per_trace = parse_node_durations(
        [_line("node_rerank", "q1", 100), _line("node_rerank", "q1", 200)]
    )

    assert per_trace == {"q1": {"node_rerank": 200}}


def test_percentile_returns_a_real_sample() -> None:
    """最近秩法: 返回的必定是样本里真有的数, 不是插值出来的."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0]

    assert percentile(values, 50) == 30.0
    assert percentile(values, 95) == 50.0
    assert percentile(values, 100) == 50.0
    assert percentile([], 50) == 0.0


def test_summarize_reports_p50_and_p95() -> None:
    stat = summarize([float(v) for v in range(1, 101)])

    assert stat["n"] == 100
    assert stat["p50_ms"] == 50.0
    assert stat["p95_ms"] == 95.0


def test_build_report_sums_each_trace_once() -> None:
    """单次合计 = 该次问答里各节点之和; 两次问答不互相污染."""
    report = build_report(
        {
            "q1": {"node_a": 100, "node_b": 200},
            "q2": {"node_a": 300, "node_b": 400},
        },
        "logs/app_x.log",
    )

    assert report["queries"] == 2
    # 两个样本的 P50 取靠前的那个 (最近秩法), 不是两者的平均
    assert report["nodes"]["node_a"]["p50_ms"] == 100.0
    assert report["per_query_total"]["mean_ms"] == 500.0


def test_render_markdown_covers_every_node() -> None:
    report = build_report({"q1": {"node_a": 100, "node_b": 200}}, "logs/app_x.log")
    table = render_markdown(report)

    assert "`node_a`" in table and "`node_b`" in table
    assert "单次问答合计" in table
