"""RAG 全链路 (Issue 10, DESIGN.md §3): 索引 / 混合检索 / 软删过滤.

- embeddings.py     embedding 抽象 (可切换, 默认 bge-m3 本地模型)
- chunking.py       文档切分 (chunk_size/overlap 按类型调优, metadata 保留)
- milvus.py         collection 重建 (全量策略) + 混合检索 (稠密+稀疏)
- query_rewrite.py  检索前 LLM 改写 (失败降级原 query)
- rerank.py         rerank 抽象 (必配链路, 本地 bge-reranker 或降级)
- retriever.py      对外门面: {kb_id, query} → {chunks[]} (QA.md Q7)

外部接口: retriever.search_kb / 索引任务经 milvus.ensure_collection +
insert_chunks 落库; 二期 Agentic RAG (QA.md Q21) 在本包内演进, 接口不变.
"""

from .retriever import search_kb  # noqa: F401
