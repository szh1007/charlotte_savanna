"""Rerank 抽象 - 必配链路, 实现可切换.

search_kb 门面无条件调用 rerank (架构必配); 具体实现按配置切换:
- BGEReranker: 本地 bge-reranker-v2-m3 (FlagReranker 跨编码器, 对
  query x passage 打分, 精度高), 懒加载模型, 仅接受已存在的本地目录
- NoopReranker: 配置留空 **或本地模型未找到** 时降级 (保持召回顺序,
  warning 日志明示原因) - 模型由用户经 modelscope 预下载 (主动行为),
  缺失降级/报错, 不触发库级自动下载

FlagEmbedding 依赖在 requirements.txt (新增, 与 rag_knowledge
参考实现同款); 测试用 FakeReranker 注入 (不加载真实模型).
"""

import logging
from typing import NamedTuple, Protocol

from ..api import config

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    """Rerank 抽象: 输入 query + 候选片段列表, 返回按相关度降序的片段."""

    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]: ...


class BGEReranker:
    """本地 bge-reranker-v2-m3 实现 (FlagReranker, 模型懒加载).

    model_name 传入**已解析的本地目录** (get_reranker 工厂 resolve 成功后才
    构造本类, 保证路径存在), 不触发 FlagEmbedding 的 hub 自动下载.
    """

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
    """降级实现: 无可用精排模型时保持召回顺序 (不精排).

    reason 记录降级原因 (未配置 / 本地模型缺失), rerank 时 warning 日志展示.
    """

    def __init__(self, reason: str = "未配置 CHARPLOT_RERANKER_MODEL"):
        self._reason = reason

    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]:
        logger.warning("rerank 降级不精排 (原因: %s), 保持召回顺序", self._reason)
        return [dict(p, score=p.get("score", 0.0)) for p in passages[:top_k]]


_reranker_instance = None


class _LocalModel(NamedTuple):
    """reranker 本地来源解析结果: path=None 即降级, reason 给出原因.

    configured 区分「配置留空」与「配置了但本地缺失」—— 两者都降级,
    但只有后者值得 warning (前者是用户没启用的正常状态).
    """

    path: str | None
    reason: str
    configured: bool


def _resolve_local_model() -> _LocalModel:
    """解析 reranker 本地模型 (降级决策的**唯一来源**).

    get_reranker 与 rerank_status 共用同一处判断, 避免「运行时事实」与
    「健康检查口径」两处漂移.
    """
    model_ref = config.RERANKER_MODEL.strip()
    if not model_ref:
        return _LocalModel(None, "未配置 CHARPLOT_RERANKER_MODEL", configured=False)
    local_path = config.resolve_local_model_path(model_ref)
    if local_path is None:
        return _LocalModel(None, f"本地模型未找到: {model_ref}", configured=True)
    return _LocalModel(local_path, "", configured=True)


def rerank_status() -> dict:
    """rerank 运行时状态 (供 /ai/health 暴露): 真实精排 or 降级 + 原因.

    README §1/§2 把 rerank 写作「必配链路」是架构意图, 本地模型缺失时
    实现走 Noop 降级 —— 本函数把运行时事实显式暴露, 两者不再打架.
    """
    resolved = _resolve_local_model()
    if resolved.path is None:
        return {"degraded": True, "reason": resolved.reason}
    return {"degraded": False, "model": resolved.path}


def get_reranker() -> Reranker:
    """按配置构建 Reranker (惰性单例; 测试可 monkeypatch 本函数注入假件).

    降级决策点: 配置值留空 → Noop (未配置); 非空但本地目录不存在 →
    Noop 降级并 warning 明示缺失路径 (不触发 FlagEmbedding 自动下载);
    本地存在 → BGEReranker(已解析的本地路径).
    """
    global _reranker_instance
    if _reranker_instance is None:
        resolved = _resolve_local_model()
        if resolved.path is None:
            if resolved.configured:
                logger.warning(
                    "reranker %s, 检索降级不精排; 期望下载到 "
                    "%s/models/BAAI/bge-reranker-v2-m3 后重启生效",
                    resolved.reason,
                    config.MODELSCOPE_ROOT,
                )
            _reranker_instance = NoopReranker(reason=resolved.reason)
        else:
            _reranker_instance = BGEReranker(model_name=resolved.path)
    return _reranker_instance
