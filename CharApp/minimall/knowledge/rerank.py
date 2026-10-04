"""Rerank 接入 (L5-a) - 本地 bge-reranker-v2-m3, 缺失时降级不精排.

搬运自 `project/charplot/rag/rerank.py` (**三级降级分支整段保留**), 改动只有一处:
配置从 charplot 的模块级常量换成传进来的 `KnowledgeConfig`.

三条分支各自的含义:

1. **配了且本地有** → `BGEReranker` (FlagReranker 跨编码器, 对 query/passage
   逐对打分, 精度比向量相似度高). 模型加载前已经解析成**存在**的本地目录,
   FlagEmbedding 不会再从 hub 下载.
2. **配了但本地没有** → `NoopReranker`, 保持召回顺序, **构造期 warning 明示缺哪
   一份**. 精排是增强不是链路前提: 缺它检索照样能用, 只是排序粗一点.
3. **没配** (`CHARAPP_RERANKER_MODEL` 为空) → 同样是 Noop, 但**构造期不打
   warning** —— 那是"用户没启用"的正常状态, 不是缺件.

   (两条 Noop 分支在**检索时**都会由 `NoopReranker.rerank` 按原因提示一句:
   "降级了"这件事只在启动那一行说一次的话, 排查时很容易漏掉它.)

降级决策只在 `_resolve_local_model` 一处做, `get_reranker` 与 `rerank_status`
共用它 —— 否则「运行时事实」与「健康检查口径」会各自漂.
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Protocol

from CharApp.minimall.config import KnowledgeConfig

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    """Rerank 抽象: 输入 query + 候选片段, 返回按相关度降序的片段.

    **可选**: 无参 `preload()` = 能提前把模型读进内存 (启动预热会调它; 没有就跳过,
    见 `embeddings.Embedder` 的同一条说明).
    """

    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]: ...


class BGEReranker:
    """本地 bge-reranker-v2-m3 实现 (FlagReranker, 模型懒加载).

    `model_name` 传的是**已解析的本地目录** (工厂 resolve 成功后才构造本类),
    不触发 FlagEmbedding 的 hub 自动下载.
    """

    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        model_name: str | None = None,
        device: str | None = None,
    ) -> None:
        self._model_name = model_name or config.reranker_model
        self._device = device or config.reranker_device
        self._use_fp16 = config.reranker_fp16
        self._model = None

    def _get_model(self):
        if self._model is None:
            from FlagEmbedding import FlagReranker

            logger.info(
                "初始化 reranker 模型 (model=%s, device=%s, fp16=%s)",
                self._model_name,
                self._device,
                self._use_fp16,
            )
            self._model = FlagReranker(
                self._model_name,
                device=self._device,
                use_fp16=self._use_fp16,
            )
        return self._model

    def preload(self) -> None:
        """把模型加载进内存 (不打分) —— `prewarm` 调它, 见 knowledge/prewarm.py."""
        self._get_model()

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
    """降级实现: 无可用精排模型时保持召回顺序 (不精排).

    `reason` 记录降级原因 (未配置 / 本地模型缺失), rerank 时 warning 展示 ——
    每一次检索都提醒一遍, 免得"降级了"这件事只有启动那一刻的日志知道.
    """

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]:
        logger.warning("rerank 降级不精排 (原因: %s), 保持召回顺序", self._reason)
        return [dict(p, score=p.get("score", 0.0)) for p in passages[:top_k]]


class _LocalModel(NamedTuple):
    """reranker 本地来源解析结果: path=None 即降级, reason 给出原因.

    `configured` 区分「配置留空」与「配置了但本地缺失」—— 两者都降级, 但只有
    后者值得 warning (前者是用户没启用的正常状态).
    """

    path: str | None
    reason: str
    configured: bool


_reranker_instance: Reranker | None = None


def _resolve_local_model(config: KnowledgeConfig) -> _LocalModel:
    """解析 reranker 本地模型 (降级决策的**唯一来源**)."""
    model_ref = config.reranker_model.strip()
    if not model_ref:
        return _LocalModel(None, "未配置 CHARAPP_RERANKER_MODEL", configured=False)
    local_path = config.resolve_local_model_path(model_ref)
    if local_path is None:
        return _LocalModel(None, f"本地模型未找到: {model_ref}", configured=True)
    return _LocalModel(local_path, "", configured=True)


def rerank_status(config: KnowledgeConfig) -> dict:
    """rerank 运行时状态 (真实精排 or 降级 + 原因).

    「精排是必配链路」是架构意图, 而本地模型缺失时实现走 Noop —— 本函数把**运行时
    事实**显式暴露出来, 两者不再打架 (charplot 那边由 /ai/health 调它, 这里留给
    用例与排查).
    """
    resolved = _resolve_local_model(config)
    if resolved.path is None:
        return {"degraded": True, "reason": resolved.reason}
    return {"degraded": False, "model": resolved.path}


def get_reranker(config: KnowledgeConfig) -> Reranker:
    """按配置构建 Reranker (惰性单例; 用例可 `reset_reranker` 或 monkeypatch).

    降级决策点: 配置留空 → Noop (未配置); 非空但本地目录不存在 → Noop + warning
    明示缺失路径 (不触发 FlagEmbedding 自动下载); 本地存在 → `BGEReranker`
    (已解析的本地路径).
    """
    global _reranker_instance
    if _reranker_instance is None:
        resolved = _resolve_local_model(config)
        if resolved.path is None:
            if resolved.configured:
                logger.warning(
                    "reranker %s, 检索降级不精排 (设计内的降级, 不是故障): "
                    "把 %s 放到 modelscope 根目录 %s 下再重启即可恢复精排",
                    resolved.reason,
                    config.reranker_model,
                    config.modelscope_root,
                )
            _reranker_instance = NoopReranker(reason=resolved.reason)
        else:
            _reranker_instance = BGEReranker(config, model_name=resolved.path)
    return _reranker_instance


def reset_reranker() -> None:
    """把单例丢掉 —— **只给用例用** (与 `embeddings.reset_embedder` 同一条)."""
    global _reranker_instance
    _reranker_instance = None


__all__ = [
    "BGEReranker",
    "NoopReranker",
    "Reranker",
    "get_reranker",
    "rerank_status",
    "reset_reranker",
]
