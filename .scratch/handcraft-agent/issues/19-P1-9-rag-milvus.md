# 19-P1-9 — RAG（摄取 + Milvus 检索 + 引用溯源）

**What to build:** RAG 全链路：文档摄取（pypdf 解析 PDF → 清洗去重 → 元数据提取 → chunk size≈500/overlap≈50 → 向量化 → Milvus upsert，增量更新走 upsert）；检索（collection support_knowledge，HNSW/COSINE/dim 1536，检索强制 tenant_id 过滤 #32，category 组合过滤；粗召回 top_k=20 → 取 top-5 喂模型）；答案带引用溯源（#29：doc_id + source + chunk 文本）；检索失败走 FAQ 降级（与 #18 衔接）。

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] 摄取脚本：PDF → chunk → 向量 → Milvus upsert 闭环（#45/#46）
- [ ] 检索：tenant 强制过滤（跨租户检索零结果 #32）、category 过滤
- [ ] 引用溯源：答案附 citations（doc_id + source）（#29）
- [ ] 摄取质量：chunk 尺寸/overlap 参数化可调（#46）
- [ ] 降级衔接：Milvus 故障 → FAQ 匹配（#19）
