# 10 — Milvus 索引与检索

**Status:** ready-for-agent

**Blocked by:** 09 — 知识库管理链路

**What to build:** 企业级 RAG 全链路落地，替换 09 的 stub 索引：文档切分（chunk_size/overlap 调优，metadata 保留来源/文档 id/有效标记）→ embedding（模型接入抽象可切换）→ 写入 Milvus collection（含稀疏索引）；检索 = 混合检索（稠密向量 + BM25 稀疏融合）+ query rewriting + rerank（必配）→ Top-K 片段；软删除过滤（Django is_deleted + Milvus metadata 有效标记，检索时 filter 排除，移除立即生效）；全量重建策略（任何变更触发重建）。

**Acceptance criteria:**
- [ ] 真实索引：文档切分 → embedding → Milvus 入库，索引进度 SSE 可见
- [ ] 混合检索 + query rewriting + rerank 全链路生效，Top-K 结果相关
- [ ] 软删除文档检索不命中（立即生效），恢复后重新命中
- [ ] 全量重建：变更（增/删/改文档）触发重建，完成数据一致
- [ ] embedding 模型可切换（配置抽象）
- [ ] 检索接口可被知识管道（11/07）调用

**References:** DESIGN.md §7 步骤 10；PRD C-3；SPEC §7.2 / Q18a、Q18b、Q18c
