"""精排窗口的动态档位.

窗口决定 padding 宽度, 而 reranker 的 attention 是平方复杂度 —— 一律按模型上限
(2048) 跑意味着大量 pad 出来的无效计算. 这里按本批候选里**最长**的那条向上取档:
短文本走 1024, 真出现长文本才升到 2048.
"""

from __future__ import annotations

from app.rag.query import rerank_service
from app.rag.query.config import RERANK_LENGTH_TIERS, RERANK_MAX_INPUT_TOKENS
from app.shared.config.reranker_config import reranker_config


def _with_token_lengths(monkeypatch, lengths: dict[str, int]):
    """按文本查表返回 token 数, 免得测试里真去加载模型."""

    def fake(text: str) -> int:
        return lengths.get(text, 1)

    monkeypatch.setattr(rerank_service.infra_model, "reranker_compute_token_num", fake)


def test_short_batch_uses_the_narrow_tier(monkeypatch):
    """整批都很短时用最窄的档 —— 这是这个优化的主要收益来源."""
    _with_token_lengths(monkeypatch, {"q1a1": 300, "q2a2": 500})

    picked = rerank_service._pick_rerank_max_length([["q1", "a1"], ["q2", "a2"]])

    assert picked == RERANK_LENGTH_TIERS[0]


def test_one_long_candidate_upshifts_the_whole_batch(monkeypatch):
    """只要有一条超档, 整批就得升档 —— 它自己不能被截断.

    (同一批共用一次前向, 没法给单条开小灶; 按最长的那条取档是唯一正确的选择.)
    """
    _with_token_lengths(monkeypatch, {"q1a1": 200, "q2a2": 1300})

    picked = rerank_service._pick_rerank_max_length([["q1", "a1"], ["q2", "a2"]])

    assert picked == RERANK_LENGTH_TIERS[1]


def test_length_is_measured_on_the_concatenated_pair(monkeypatch):
    """按「query + answer」整体量长度 —— reranker 就是这么拼的."""
    _with_token_lengths(monkeypatch, {"q1a1": 1100})

    picked = rerank_service._pick_rerank_max_length([["q1", "a1"]])

    assert picked == RERANK_LENGTH_TIERS[1]


def test_batch_longer_than_every_tier_falls_back_to_the_top(monkeypatch):
    """超过最高档时不报错, 退回最高档.

    正常链路上不会出现 —— 压缩逻辑 (`RERANK_MAX_INPUT_TOKENS`) 已经把超限的
    候选先处理掉了; 这条兜的是「压缩被关掉 / 阈值被调大」的情形.
    """
    _with_token_lengths(monkeypatch, {"q1a1": RERANK_MAX_INPUT_TOKENS + 5000})

    picked = rerank_service._pick_rerank_max_length([["q1", "a1"]])

    assert picked == RERANK_LENGTH_TIERS[-1]


def test_top_tier_matches_the_model_window_and_compression_threshold():
    """三处上限必须对齐: 压缩阈值 / 模型窗口 / 最高档.

    对不齐就会「压缩到 A 却被模型按 B 截掉」, 或在压缩阈值之上还有没处理的长文本.
    """
    assert RERANK_LENGTH_TIERS[-1] == RERANK_MAX_INPUT_TOKENS
    assert RERANK_LENGTH_TIERS[-1] == reranker_config.bge_reranker_max_length, (
        "最高档与模型窗口 (RK_BGE_RERANKER_MAX_LENGTH) 对不上"
    )
    tiers = list(RERANK_LENGTH_TIERS)
    assert tiers == sorted(tiers), "档位要递增"
