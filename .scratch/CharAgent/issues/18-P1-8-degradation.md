# 18-P1-8 — 业务降级兜底

**What to build:** 降级阶梯：#19 错误码全部挂降级路径——LLM_DOWN（熔断打开）→ 模板回复 + 转人工建议；LLM_TIMEOUT → 返回已算部分结果或模板；RAG_DOWN（Milvus/embedding 故障）→ FAQ 规则匹配（Redis 精确/相似命中）；TOOL_ERROR（自纠错无效）→ workflow 兜底路径 / 转人工；RATE_LIMITED → 排队或 429 + 重试提示；CANCELLED 正常收尾。

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] LLM 失败 → 模板回复 + 转人工建议（LLM_DOWN/LLM_TIMEOUT）
- [ ] RAG 失败 → FAQ 匹配降级（RAG_DOWN）
- [ ] 工具全失败且自纠错无效 → workflow 兜底 / 转人工（TOOL_ERROR）
- [ ] 限流 → 排队/429（RATE_LIMITED）
- [ ] 测试：每个错误码的降级路径验证（#19）
