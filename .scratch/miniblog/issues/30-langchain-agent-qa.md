# 30 — LangChain Agent & QA 端点

**What to build:** DeepSeek Agent（PostgresSaver 记忆）、Milvus 检索器、QA 对话端点（显式/隐式找帖逻辑）。

**Blocked by:** 29 — Milvus & Embedding, 27 — Celery Embedding 同步

**Status:** ready-for-agent

- [ ] 创建 `miniblog/llm/agent.py`：`init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})` + `create_agent(model, checkpointer=PostgresSaver)`（参考 `demo/LangChain_20260714/_10_RAG/10_5_RAG.py`）
- [ ] 创建 `miniblog/llm/retriever.py`：
  - `search_posts(query, limit, min_score)`：embed_query → Milvus search → 过滤 score > min_score → 返回帖子列表（含 score）
  - `summarize_post(post)`：LLM 生成单篇帖子的一句话内容摘要
- [ ] `POST /api/assistant/chat`：
  - 接收 `{query, thread_id?}`（thread_id 为空时自动生成）
  - Agent 先判断用户意图（是否明确要求找帖子）
  - **明确找帖**：Milvus 检索 → 返回 Top 5（每篇含摘要），前端提供展开按钮 → 展开后展示所有 score > 0.6 的帖子（最多 20 篇）
  - **未明确找帖**：Milvus 检索 Top 2 → 回答用户问题 → 在回答末尾追加"是否需要我帮您查询【关键词】相关的帖子？"
  - 返回 `{answer, posts: [...], has_more: bool, total_found: N, thread_id}`
- [ ] `GET /api/assistant/sessions`：用户的历史会话列表（从 PostgresSaver 按 thread_id 查询）
- [ ] `DELETE /api/assistant/sessions/{thread_id}`：删除指定会话
