"""对照工具的分组 / 均值 / 臂内极差 —— 全是纯函数, 拿合成报告钉住语义.

极差那一条最要紧: **单趟时必须返回 None, 不能返回 0**. 返回 0 等于宣称"这臂很稳",
而实际上只跑了一趟, 稳不稳根本无从谈起 —— 那正是「一次跑出来的差不能当结论」
这条纪律要防的东西.
"""

from __future__ import annotations

import pytest

from app.rag_eval.compare import (
    compare,
    group_by_arm,
    render_markdown,
    summarize_arm,
)


def _report(arm: str, recall: float, must_hit: float, precision: float, name: str):
    return {
        "_来源": name,
        "运行配置": {"臂": arm},
        "汇总结果": {
            "用例总数": 2,
            "平均主体命中率": 0.9,
            "分层汇总": {
                "普通检索": {
                    "平均召回率": recall,
                    "平均必命中率": must_hit,
                    "平均MRR@1": 0.8,
                    "平均NDCG@1": 0.8,
                },
                "最终重排结果": {
                    "平均召回率": recall,
                    "平均必命中率": must_hit,
                    "平均MRR@1": 0.8,
                    "平均NDCG@1": 0.8,
                    "平均精确率": precision,
                },
            },
        },
    }


def test_groups_by_arm_not_by_filename():
    """分组看报告里的臂名 —— 文件名是人起的, 会写错."""
    reports = [
        _report("rerank-on", 0.8, 0.9, 0.5, "随意起的名字A.json"),
        _report("rerank-off", 0.7, 0.8, 0.4, "随意起的名字B.json"),
        _report("rerank-on", 0.82, 0.9, 0.52, "随意起的名字C.json"),
    ]

    groups = group_by_arm(reports)

    assert sorted(groups) == ["rerank-off", "rerank-on"]
    assert len(groups["rerank-on"]) == 2


def test_old_reports_without_arm_land_in_a_labelled_bucket():
    """没有臂名的旧报告不能被丢掉, 也不能混进某个臂 —— 单列一组."""
    groups = group_by_arm([{"运行配置": {}, "汇总结果": {}}])

    assert list(groups) == ["未标注"]


def test_mean_and_spread_over_two_runs():
    arm = summarize_arm(
        [
            _report("rerank-on", 0.80, 0.90, 0.50, "a.json"),
            _report("rerank-on", 0.84, 0.92, 0.54, "b.json"),
        ]
    )

    recall = arm["分层"]["普通检索"]["平均召回率"]
    assert recall["均值"] == 0.82
    assert recall["极差"] == 0.04
    assert recall["各趟"] == [0.80, 0.84]


def test_single_run_has_no_spread():
    """一趟就是没有极差可言, 记 None (不是 0)."""
    arm = summarize_arm([_report("rerank-on", 0.8, 0.9, 0.5, "a.json")])

    assert arm["分层"]["普通检索"]["平均召回率"]["极差"] is None


def test_precision_only_on_the_final_layer():
    """精确率只报最终层 —— 前几层的分母是召回池大小, 是人为的."""
    arm = summarize_arm([_report("rerank-on", 0.8, 0.9, 0.5, "a.json")])

    assert "平均精确率" not in arm["分层"]["普通检索"]
    assert arm["分层"]["最终重排结果"]["平均精确率"]["均值"] == 0.5


def test_delta_is_computed_only_between_exactly_two_arms():
    two = compare(
        [
            _report("rerank-on", 0.80, 0.90, 0.50, "a.json"),
            _report("rerank-off", 0.84, 0.88, 0.44, "b.json"),
        ]
    )

    assert two["两臂之差"]["rerank-off - rerank-on / 普通检索"]["平均召回率"] == 0.04

    three = compare(
        [
            _report("arm-a", 0.8, 0.9, 0.5, "a.json"),
            _report("arm-b", 0.8, 0.9, 0.5, "b.json"),
            _report("arm-c", 0.8, 0.9, 0.5, "c.json"),
        ]
    )
    assert three["两臂之差"] == {}


def test_markdown_renders_both_arms_and_the_delta():
    text = render_markdown(
        compare(
            [
                _report("rerank-on", 0.80, 0.90, 0.50, "a.json"),
                _report("rerank-off", 0.84, 0.88, 0.44, "b.json"),
            ]
        )
    )

    assert "臂: rerank-on" in text
    assert "臂: rerank-off" in text
    assert "两臂之差" in text


def test_rank_metric_keys_follow_the_report_not_a_hardcoded_k():
    """MRR/NDCG 的 K 跟着 `RERANK_MAX_TOPK` 走, 键名会从 `@8` 变成 `@1`.

    这是回归用例: 早先这里写死了 `平均MRR@8`, 而参数一改成 1, 报告键就变成了 `@1` ——
    取值取不到, 对照表**静默少报两项**, 不报错, 读的人只会以为"就这几项".
    """
    arm = summarize_arm([_report("rerank-on", 0.8, 0.9, 0.5, "a.json")])

    final = arm["分层"]["最终重排结果"]
    assert "平均MRR@1" in final
    assert "平均NDCG@1" in final
    assert final["平均MRR@1"]["均值"] == 0.8


def test_missing_rank_metrics_fail_loudly():
    """连键都找不到说明报告不是这套口径生成的 —— 当场报错, 别出一张缺项的表."""
    broken = _report("rerank-on", 0.8, 0.9, 0.5, "a.json")
    for layer in broken["汇总结果"]["分层汇总"].values():
        for key in [k for k in layer if k.startswith(("平均MRR@", "平均NDCG@"))]:
            del layer[key]

    with pytest.raises(ValueError, match="MRR"):
        summarize_arm([broken])
