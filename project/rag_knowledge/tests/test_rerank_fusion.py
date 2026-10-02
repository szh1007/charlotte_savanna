"""精排分数与 RRF 先验的融合.

只按精排分数排等于「一票否决」: reranker 打偏时没有任何兜底, 而它在这批中文
长文本上确实会偏 (实测 MRR@5 被拖到 0.52, 而 RRF 自己的顺序有 0.79).
融合之后精排仍能翻盘, 但它打平或打偏时 RRF 的顺序会说话.
"""

from __future__ import annotations

from app.rag.query import rerank_service
from app.rag.query.config import RERANK_FUSION_ALPHA


def _fake_reranker(monkeypatch, scores: list[float]):
    """把打分和分词都挡掉 —— 这两个接口背后是真模型, 单元测试不该去加载它."""

    def fake_scores(pairs, max_length=None):
        return scores

    monkeypatch.setattr(
        rerank_service.infra_model, "reranker_compute_scores", fake_scores
    )
    monkeypatch.setattr(
        rerank_service.infra_model, "reranker_compute_token_num", lambda text: 10
    )


def _merge_with_scores(
    monkeypatch, rrf_scores: list[float], rerank_scores: list[float], types=None
):
    """用给定的两组分数跑一遍排序, 返回排序后的 chunk_id 列表."""
    types = types or ["milvus"] * len(rrf_scores)
    merged = [
        {"chunk_id": str(i), "rrf_score": r, "score": 0.0, "type": t}
        for i, (r, t) in enumerate(zip(rrf_scores, types))
    ]
    _fake_reranker(monkeypatch, rerank_scores)
    rerank_service._list_score_and_rank(merged, [["q", "a"]] * len(rrf_scores))
    return [c["chunk_id"] for c in merged]


def test_rrf_prior_breaks_ties(monkeypatch):
    """精排打平时, RRF 排名靠前的应当在前 —— 这就是融合的意义."""
    # RRF: 0 号最靠前; 精排给两者相同分数
    order = _merge_with_scores(monkeypatch, [0.016, 0.008], [0.5, 0.5])

    assert order == ["0", "1"]


def test_rerank_can_still_override_the_prior(monkeypatch):
    """精排差得够多时仍能翻盘 —— 融合不是把精排架空."""
    # 1 号的 RRF 靠后, 但精排远高于 0 号
    order = _merge_with_scores(monkeypatch, [0.016, 0.008], [0.1, 0.95])

    assert order == ["1", "0"]


def test_fusion_weight_matches_config(monkeypatch):
    """融合分数要正好是 alpha*rrf_norm + (1-alpha)*rerank —— 防止公式被改歪."""
    merged = [
        {"chunk_id": "a", "rrf_score": 1.0, "score": 0.0, "type": "milvus"},
        {"chunk_id": "b", "rrf_score": 0.0, "score": 0.0, "type": "milvus"},
    ]
    _fake_reranker(monkeypatch, [0.2, 0.4])

    rerank_service._list_score_and_rank(merged, [["q", "a"], ["q", "b"]])

    by_id = {c["chunk_id"]: c for c in merged}
    assert (
        by_id["a"]["score"]
        == RERANK_FUSION_ALPHA * 1.0 + (1 - RERANK_FUSION_ALPHA) * 0.2
    )
    assert (
        by_id["b"]["score"]
        == RERANK_FUSION_ALPHA * 0.0 + (1 - RERANK_FUSION_ALPHA) * 0.4
    )


def test_identical_rrf_scores_do_not_divide_by_zero(monkeypatch):
    """所有 RRF 分数相同时 span 为 0 —— 不能炸, 也不该改变排序."""
    order = _merge_with_scores(monkeypatch, [0.01, 0.01], [0.3, 0.7])

    assert order == ["1", "0"], "精排分高的在前"


def test_web_docs_get_a_neutral_prior(monkeypatch):
    """联网那一路没进 RRF, 不该被系统性地压到最低.

    它拿到的先验应当与 RRF 里**偏低的那一档**持平 (0.5), 而不是被缩到 0.
    """
    # 0 号联网 (rrf_score 占位 0), 1 号 RRF 最低分, 2 号 RRF 最高分
    merged = [
        {"chunk_id": "web", "rrf_score": 0.0, "score": 0.0, "type": "web_search"},
        {"chunk_id": "low", "rrf_score": 0.008, "score": 0.0, "type": "milvus"},
        {"chunk_id": "high", "rrf_score": 0.016, "score": 0.0, "type": "milvus"},
    ]
    # 精排给三者相同分数, 排序完全由 RRF 先验决定
    _fake_reranker(monkeypatch, [0.5, 0.5, 0.5])

    rerank_service._list_score_and_rank(merged, [["q", "a"]] * 3)

    by_id = {c["chunk_id"]: c["score"] for c in merged}
    assert by_id["web"] == by_id["low"], "联网结果的先验应当与 RRF 偏低档持平"
    assert by_id["high"] > by_id["web"], "RRF 最高的仍要排在前面"


# ---------------------------------------------------------------------------
# 文档标题片降权
#
# 标题片 = 文档开头那块 `# 项目名` + 项目简介. 它含项目名、又通篇讲这个项目,
# 与任何带主体名的问题字面重合度都最高 —— 实测 40 题里 35 题它都进了候选池,
# 而真正拿它当答案的只有 2 题 (「XX 是什么项目」). 降权而不排除的理由见
# `DOC_HEAD_SCORE_FACTOR`.
# ---------------------------------------------------------------------------


def _merge_with_titles(
    monkeypatch, title_a: str, title_b: str, rerank_scores: list[float]
) -> tuple[list[str], list[dict]]:
    """用两条候选跑一遍排序, 只让 title 不同 (RRF 先验给成一样)."""
    merged = [
        {
            "chunk_id": "a",
            "title": title_a,
            "rrf_score": 0.016,
            "score": 0.0,
            "type": "milvus",
        },
        {
            "chunk_id": "b",
            "title": title_b,
            "rrf_score": 0.016,
            "score": 0.0,
            "type": "milvus",
        },
    ]
    _fake_reranker(monkeypatch, rerank_scores)
    rerank_service._list_score_and_rank(merged, [["q", "a"]] * 2)
    return [c["chunk_id"] for c in merged], merged


def test_document_head_is_discounted_below_an_equal_chunk(monkeypatch):
    """同样精排分下, 标题片要被压到章节块之后 —— 别占着高位挤掉具体章节."""
    order, _ = _merge_with_titles(
        monkeypatch, "# 某项目 — 项目简介", "### 5.2 环境变量", [0.9, 0.9]
    )

    assert order == ["b", "a"]


def test_document_head_still_wins_when_it_is_the_only_match(monkeypatch):
    """「XX 是什么项目」那类题要能靠它作答 —— 打折不是排除."""
    order, _ = _merge_with_titles(
        monkeypatch, "# 某项目 — 项目简介", "### 5.2 环境变量", [0.99, 0.05]
    )

    assert order == ["a", "b"]


def test_section_titles_are_not_discounted(monkeypatch):
    """只有 H1 标题片打折, `##` / `###` 的章节块一律不动."""
    _, merged = _merge_with_titles(
        monkeypatch, "## 5. 快速开始", "### 5.2 环境变量", [0.5, 0.5]
    )

    assert merged[0]["score"] == merged[1]["score"]


def test_document_head_factor_of_one_disables_the_fix(monkeypatch):
    """系数取 1.0 时完全等价于不干预 —— 留作回退开关."""
    monkeypatch.setattr(rerank_service, "DOC_HEAD_SCORE_FACTOR", 1.0)

    _, merged = _merge_with_titles(
        monkeypatch, "# 某项目 — 项目简介", "### 5.2 环境变量", [0.5, 0.5]
    )

    assert merged[0]["score"] == merged[1]["score"]
