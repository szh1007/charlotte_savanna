# 10 — Milvus 索引与检索

**Status:** done

**Blocked by:** 09 — 知识库管理链路

**What to build:** 企业级 RAG 全链路落地，替换 09 的 stub 索引：文档切分（chunk_size/overlap 调优，metadata 保留来源/文档 id/有效标记）→ embedding（模型接入抽象可切换）→ 写入 Milvus collection（含稀疏索引）；检索 = 混合检索（稠密向量 + BM25 稀疏融合）+ query rewriting + rerank（必配）→ Top-K 片段；软删除过滤（Django is_deleted + Milvus metadata 有效标记，检索时 filter 排除，移除立即生效）；全量重建策略（任何变更触发重建）。

**Acceptance criteria:**
- [x] 真实索引：文档切分 → embedding → Milvus 入库，索引进度 SSE 可见
- [x] 混合检索 + query rewriting + rerank 全链路生效，Top-K 结果相关
- [x] 软删除文档检索不命中（立即生效），恢复后重新命中
- [x] 全量重建：变更（增/删/改文档）触发重建，完成数据一致
- [x] embedding 模型可切换（配置抽象）
- [x] 检索接口可被知识管道（11/07）调用

**References:** DESIGN.md §7 步骤 10；PRD C-3；SPEC §7.2 / Q18a、Q18b、Q18c

**实现摘要 (2026-08-24):**

**FastAPI 侧 `rag/` 模块**（`project/charplot/rag/`，DESIGN §3 目录对齐）
- `embeddings.py` — Embedder 抽象（embed_documents/embed_query → {dense, sparse}），工厂 `get_embedder` 按配置切换（当前 `bge-m3`：pymilvus `BGEM3EmbeddingFunction` 本地模型, 稠密+稀疏一次出, 原生 L2 归一化适配 IP 检索；CSR 稀疏矩阵拆解为 {idx: weight} 字典）
- `chunking.py` — 按文档类型调优切分（langchain RecursiveCharacterTextSplitter：md/txt 500/50、html 800/80、pdf/docx/pptx 600/60），metadata 保留 doc_id/title/filename/chunk_index/valid
- `milvus.py` — collection 生命周期 + 混合检索：`ensure_collection`（drop+create 全量重建, 物理剔除软删；schema 含双向量字段 + 来源 metadata；pymilvus 3.0 API: `prepare_index_params` 字符串 index_type/metric_type, 稠密 HNSW/IP + 稀疏 SPARSE_INVERTED_INDEX/IP）；`hybrid_search`（双 AnnSearchRequest + WeightedRanker 加权融合, filter 排除软删）；`_build_filter_expr`（`valid == true and doc_id not in [...]`）
- `query_rewrite.py` — LLM 改写查询（DeepSeek, pipeline.llm 同款单例）, 失败/关闭降级原 query 不阻塞检索
- `rerank.py` — Reranker 抽象（必配链路无条件调用）：`BGEReranker`（本地 bge-reranker-v2-m3, FlagReranker 懒加载）/ `NoopReranker`（配置留空降级保持召回顺序）；requirements 新增 `FlagEmbedding==1.4.0`
- `retriever.py` — 对外门面 `search_kb(kb_id, query, top_k)`：rewrite → embed → 软删过滤 → 混合检索 → rerank → Top-K（召回 `CHARPLOT_RETRIEVE_TOP_K=20`, 精排 `CHARPLOT_RERANK_TOP_K=5`）

**FastAPI 侧 `api/`**
- `tasks.py` — `_run_kb_index_task` stub → 真实索引：per-doc 流水线（取内容 → 解析复用 `pipeline/parsers` → 切分 → 向量化暂存行）→ `ensure_collection` + `insert_chunks` 全量重建入库；SSE 阶段契约不变（parsing → chunking/embedding 交替 → indexing → done/error, CONTRACT §6.5）；移除 stub sleep 配置
- `server.py` / `schemas.py` — 新增 `POST /ai/kb/search` `{kb_id, query, top_k?}` → `{chunks: [{doc_id, title, filename, chunk_index, content, score}]}`（QA.md Q7：片段检索不是答案）
- `django_client.py` — `fetch_kb_document_content`（索引解析输入）+ `fetch_kb_deleted_doc_ids`（同步版, 检索软删集合实时查询）
- `config.py` — RAG 配置组（MILVUS_URL/EMBEDDING_*/CHUNK_*/RERANKER_*/RETRIEVE_TOP_K/RERANK_TOP_K/QUERY_REWRITE），`.env.example` 同步
- `pipeline/sources/kb_source.py` — 占位 → `KbSource(kb_id)` 真实检索（SearchSource 协议适配, metadata 带 kb_id/doc_id/chunk_index/score；Issue 11 在 build_sources 注册）

**Django 侧**（`app/charplot/`）
- 新增内部端点（CONTRACT §6.6 落地, X-Internal-Token）：
  - `GET /api/charplot/kb/documents/{id}/content/` → `{filename(原始名 title), content_base64}`（软删文档可读, 恢复后重索引需要）
  - `GET /api/charplot/kb/{id}/deleted-doc-ids/` → `{deleted_doc_ids: [...]}`（软删立即生效：检索实时查询构造 filter, 恢复自动移除重新命中, 无需等重建）
- 测试 `test_knowledge_base.py` 新增 2 个端点测试类（9 个测试）

**测试**：FastAPI 侧 `test_kb_rag.py`（12 个：rewrite 改写/降级/开关、filter 表达式、全链路检索 + 软删过滤 + 精排、端点契约、KbSource 映射、索引解析失败语义）+ `test_kb_index.py` 适配真实索引（入库行结构断言）；conftest 新增 `FakeEmbedder`/`FakeMilvusClient`/`fake_rag_deps` 隔离 embedding 模型与 Milvus（不触网不加载模型）；Django 223 个全过

**关键设计决策**
- **软删立即生效（无反向调用）**：检索时实时向 Django 查软删 doc_id 集合 → Milvus filter 排除（Q18c）；重建 drop+create 物理剔除。符合 CONTRACT §6.3「不引入 Django→FastAPI 反向调用」约束
- **pymilvus 3.0 API 适配**：`IndexParams`/`MetricType` 未顶层导出, 用 `client.prepare_index_params()` + 字符串参数（参考官方 sparse 示例, 与 rag_knowledge 2.x 写法不同）
- **redis-py 8 默认 5s socket_timeout 修复**：任务体内同步 AI 操作阻塞事件循环时状态写被误杀（间歇性 TimeoutError）→ `tasks.get_redis` 显式 `socket_timeout=None`
- **测试基建**：`wait_until` 默认超时 5s → 15s、interval 0.02 → 0.05（真实索引完成路径实测 ~6s, 密集轮询饿死后台任务）

**与 DESIGN.md 的偏差**：无（§4.2 `/ai/kb/search` 契约原样落地）
