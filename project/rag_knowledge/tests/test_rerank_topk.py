"""精排后的动态截取: 断崖判据是「相对头部的累计衰减」.

两个都踩过的坑, 各有一条回归:

1. **拿相邻两点比** (`current - next`): 0.8 / 0.7 / 0.6 / 0.5 这种缓慢下滑
   每步只掉 0.1, 永远不触发, 可到第 3 个已经比头部低 25%.
2. **下限等于上限**: 断崖检测从下限处才开始向后看, 两者相等等于「前 N 条无条件
   保留」—— 8 条候选里分数从 0.95 掉到 0.05 (跨度 0.9) 也照样原样返回.
"""

from __future__ import annotations

import pytest

from app.rag.query import rerank_service

# 断崖窗口钉住: 下面这些用例测的是**判据本身** (落差会不会被检查到、全 0 会不会炸),
# 与"线上当前交付几条"无关. 2026-10-06 `RERANK_MAX_TOPK` 由 8 调到 1 之后, 依赖
# "窗口够宽"的用例全挂了 —— 根因就是它们读的是线上调参值.
CLIFF_MAX, CLIFF_MIN = 8, 1


@pytest.fixture(autouse=True)
def _pin_cliff_window(monkeypatch):
    monkeypatch.setattr(rerank_service, "RERANK_MAX_TOPK", CLIFF_MAX)
    monkeypatch.setattr(rerank_service, "RERANK_MIN_TOPK", CLIFF_MIN)


def _scored(scores: list[float]) -> list[dict]:
    return [{"chunk_id": str(i), "score": s} for i, s in enumerate(scores)]


def test_head_span_is_checked_not_only_the_tail():
    """断崖检测必须覆盖头部区间.

    回归: 下限曾等于上限 (都是 8), 循环从第 8 名才开始 —— 8 条候选里
    0.95 / 0.90 / 0.80 / 0.60 ... 这种巨大落差完全没被检查, 原样返回 8 条.
    """
    merged = _scored([0.95, 0.90, 0.80, 0.60, 0.50, 0.40, 0.20, 0.05])

    out = rerank_service._dynamic_topk(merged)

    # 断点在 0.60 那一档 (第 4 名); 下限若比它更高则保底优先
    assert len(out) < len(merged), "头部区间里的断崖必须被检查到"
    assert len(out) == max(CLIFF_MIN, 3)


def test_dynamic_topk_breaks_on_cumulative_decay():
    """缓慢下滑也要断 —— 相邻比较抓不到的那类序列.

    头部 0.90, 前 4 名每步只降 0.01 (相邻判据完全无感), 第 5 名掉到 0.60,
    相对头部已跌 33%, 应当在它前面断开 (下限更高时以保底为准).
    """
    merged = _scored([0.90, 0.89, 0.88, 0.87, 0.60, 0.59, 0.58, 0.57, 0.56, 0.55])

    out = rerank_service._dynamic_topk(merged)

    assert len(out) == max(CLIFF_MIN, 4), f"应在第 5 名之前断开, 实际留下 {len(out)} 条"


def test_dynamic_topk_keeps_the_head_intact():
    """头部区间内的正常波动不该被误断 —— 返回条数顶到上限为止."""
    merged = _scored([0.90, 0.89, 0.88, 0.87, 0.86, 0.85, 0.84, 0.83, 0.82, 0.81])

    out = rerank_service._dynamic_topk(merged)

    assert len(out) == min(CLIFF_MAX, len(merged)), "全程在头部区间内, 不该断"


def test_dynamic_topk_never_returns_fewer_than_the_floor():
    """断崖点再靠前, 也不能低于下限 —— 那是「至少给作答链路几条」的保底.

    构造: 前 `CLIFF_MIN` 条都在头部区间, 紧接着直接掉到 0.1 ——
    断点必然落在下限处, 恰好验证保底生效.
    """
    merged = _scored([0.90] * CLIFF_MIN + [0.10] * 17)

    out = rerank_service._dynamic_topk(merged)

    assert len(out) == CLIFF_MIN


def test_dynamic_topk_handles_head_score_zero():
    """头部为 0 时不该除零 —— 全是 0 的分数意味着没有断崖可言, 全给."""
    merged = _scored([0.0] * 20)

    out = rerank_service._dynamic_topk(merged)

    assert len(out) == min(CLIFF_MAX, len(merged))


def test_dynamic_topk_handles_short_list():
    """候选全都挤在头部区间时原样返回, 不越界."""
    merged = _scored([0.90, 0.89, 0.88])

    out = rerank_service._dynamic_topk(merged)

    assert out == merged
