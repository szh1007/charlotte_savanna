"""检索门面 (L5-a): `query -> chunks` 的统一入口.

重写自 `project/charplot/rag/retriever.py`, 两处按本票的形态改掉:

- **去掉 `kb_id` 与软删查询**: 本业务一个全局知识库 (单 collection), 下架文档由
  索引脚本的全量重建物理剔除 —— 检索路径上没有第二个数据源要问, 也就没有
  "Django 不可达时怎么办" 那一支.
- **整条链路是 async 的**: charplot 那份是同步 httpx + 同步模型推理, 直接搬进
  这个 asyncio 服务会**卡住事件循环** (检索一次就把别的请求全堵住 —— 这正是
  ticket #65 那条"同步工具会占满线程池"的同一个病). 这里把三段同步重活
  (`embed_query` / `hybrid_search` / `rerank`) 各丢进 `asyncio.to_thread`,
  于是并发提问时大家排队的是线程池, 不是事件循环.

链路顺序 (与 charplot 一致): query rewriting (LLM, 失败降级) → 向量化 →
混合检索 (稠密+稀疏) → rerank (缺失则保持召回顺序) → Top-K 片段.

`model` 是**可选**的: 给了才做改写 (它是会话手里的模型); 索引脚本 / 离线排查
不传它就跳过改写那一步 —— 检索本身不需要 LLM.
"""

from __future__ import annotations

import asyncio
import logging

from CharAgent.model.protocol import ChatModel
from CharApp.minimall.config import KnowledgeConfig
from CharApp.minimall.knowledge import milvus
from CharApp.minimall.knowledge.embeddings import get_embedder
from CharApp.minimall.knowledge.query_rewrite import rewrite_query
from CharApp.minimall.knowledge.rerank import get_reranker

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """政策知识库的检索门面 (每个会话一个; 重的零件在模块里共享).

    Args:
        config: 知识库配置 (向量库地址 / 模型 / 两个 K / rewrite 开关).
        model: 会话手里的模型, 只用于查询改写; None = 不改写 (见模块 docstring).

    attributes:
        (无公开属性; 零件都是惰性单例, 拿的时候才建)
    """

    def __init__(self, config: KnowledgeConfig, *, model: ChatModel | None = None):
        self._config = config
        self._model = model

    @property
    def config(self) -> KnowledgeConfig:
        """这份检索器读到的配置 (排查与用例要看的那几个值)."""
        return self._config

    async def search(self, query: str, *, top_k: int | None = None) -> list[dict]:
        """检索: 一个查询 → 相关片段列表 (精排后的 Top-K).

        Args:
            query: 查询原文 (买家的话, 或模型自己组织的检索词).
            top_k: 精排后要几条; None = 用 `config.rerank_top_k`.

        Returns:
            list[dict]: `[{id, slug, title, category, chunk_index, content, score}]`,
            按相关度降序.

        Raises:
            ValueError: 查询是空的 (调用方的问题, 不是"没查到").
            RuntimeError: 向量库不可用 (见 `milvus.hybrid_search`)。
            Exception: embedding 模型缺失时 `get_embedder` 抛的那一条
                (带下载命令的运行期错误) —— 不在这里吞掉: 检索工具会把它翻成
                「暂时查不到」, 而吞掉就没人知道 RAG 整条链路没在工作.
        """
        text = query.strip()
        if not text:
            raise ValueError("检索 query 不能为空")
        top = top_k or self._config.rerank_top_k
        rewritten = await rewrite_query(text, model=self._model, config=self._config)

        embedder = get_embedder(self._config)
        # 同步模型推理 → 线程池: 不占事件循环 (见模块 docstring)
        vector = await asyncio.to_thread(embedder.embed_query, rewritten)

        candidates = await asyncio.to_thread(
            milvus.hybrid_search,
            self._config,
            vector["dense"],
            vector["sparse"],
            self._config.retrieve_top_k,
        )

        reranker = get_reranker(self._config)
        chunks = await asyncio.to_thread(reranker.rerank, rewritten, candidates, top)
        logger.info("检索完成: 召回 %d → 精排 %d", len(candidates), len(chunks))
        return chunks


__all__ = ["KnowledgeRetriever"]
