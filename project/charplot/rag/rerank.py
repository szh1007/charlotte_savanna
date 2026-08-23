"""Rerank 抽象 (Issue 10, SPEC §7.2) - 必配链路, 实现可切换.

search_kb 门面无条件调用 rerank (架构必配); 具体实现按配置切换:
- BGEReranker: 本地 bge-reranker-v2-m3 (FlagReranker 跨编码器, 对
  query x passage 打分, 精度高), 懒加载模型
- NoopReranker: CHARPLOT_RERANKER_MODEL 留空时降级 (保持原有顺序,
  打 warning) - 模型下载/安装是用户主动行为, 不阻塞自用链路

FlagEmbedding 依赖在 requirements.txt (Issue 10 新增, 与 rag_knowledge
参考实现同款); 测试用 FakeReranker 注入 (不加载真实模型).
"""

import logging
from typing import Protocol

from ..api import config

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    """Rerank 抽象: 输入 query + 候选片段列表, 返回按相关度降序的片段."""

    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]: ...


class BGEReranker:
    """本地 bge-reranker-v2-m3 实现 (FlagReranker, 模型懒加载)."""

    def __init__(self, model_name: str | None = None, device: str | None = None):
        self._model_name = model_name or config.RERANKER_MODEL
        self._device = device or config.RERANKER_DEVICE
        self._model = None

    def _get_model(self):
        if self._model is None:
            from FlagEmbedding import FlagReranker

            logger.info(
                "初始化 reranker 模型 (model=%s, device=%s, fp16=%s)",
                self._model_name,
                self._device,
                config.RERANKER_FP16,
            )
            self._model = FlagReranker(
                self._model_name,
                device=self._device,
                use_fp16=config.RERANKER_FP16,
            )
        return self._model

    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]:
        if not passages:
            return []
        pairs = [[query, p["content"]] for p in passages]
        scores = self._get_model().compute_score(pairs, normalize=True)
        # compute_score 单条返回 float, 多条返回 list[float]
        if isinstance(scores, float):
            scores = [scores]
        ranked = sorted(zip(passages, scores), key=lambda pair: pair[1], reverse=True)
        result = [dict(p, score=float(score)) for p, score in ranked[:top_k]]
        logger.debug("rerank 完成: %d → %d 条", len(passages), len(result))
        return result


class NoopReranker:
    """降级实现: 未配置 rerank 模型时保持召回顺序 (不精排)."""

    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]:
        logger.warning(
            "未配置 CHARPLOT_RERANKER_MODEL, 检索跳过精排 (降级保持召回顺序)"
        )
        return [dict(p, score=p.get("score", 0.0)) for p in passages[:top_k]]


_reranker_instance = None


def get_reranker() -> Reranker:
    """按配置构建 Reranker (惰性单例; 测试可 monkeypatch 本函数注入假件)."""
    global _reranker_instance
    if _reranker_instance is None:
        if config.RERANKER_MODEL.strip():
            _reranker_instance = BGEReranker()
        else:
            _reranker_instance = NoopReranker()
    return _reranker_instance
