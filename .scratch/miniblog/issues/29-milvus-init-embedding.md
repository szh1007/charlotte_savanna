# 29 — Milvus 初始化 & Embedding 配置

**What to build:** Milvus 数据库/集合创建、Embedding 模型初始化、向量化工具函数。

**Blocked by:** 03 — PostgreSQL 引擎

**Status:** ready-for-agent

- [ ] 创建 `miniblog/llm/embedding.py`：`init_embeddings("openai:text-embedding-3-large")` 初始化，封装 `embed_text(text)` 和 `embed_texts(texts)` 工具函数（参考 `demo/LangChain_20260714/_10_RAG/10_3_RAG_embedding.py`）
- [ ] 创建 `miniblog/llm/milvus_client.py`：MilvusClient 连接，db/collection 初始化（参考 `demo/LangChain_20260714/_10_RAG/10_4_RAG_vecto_store.py`）
  - db_name=`MILVUS_DB_NAME`，collection_name=`MILVUS_COLLECTION_NAME`
  - dimension=3072，metric_type=COSINE
- [ ] `milvus_client` 封装：`insert_vectors(data)`, `search_vectors(query_vector, limit, output_fields)`, `delete_vectors(ids)`, `get_by_id(id)`
- [ ] `POST /api/admin/milvus/init`：管理员手动触发初始化（创建 db + collection），幂等
