# 20-P1-10 — 结构化输出 + 上下文压缩

**What to build:** 结构化输出：强制模型按 JSON schema 输出（response_format + json_schema），校验失败自动重试（#6）；上下文压缩：多轮膨胀的历史做摘要/截断（ContextCompaction），**不破坏 tool 调用结构**（#7）——压缩后的 tool_result 保持可解析语义。

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] 结构化输出：schema 校验 + 失败重试闭环（#6）
- [ ] 压缩：长历史摘要/截断，tool 调用结构不被破坏（#7）
- [ ] 压缩后 loop 可继续运行（工具结果语义完整）
- [ ] 测试：压缩边界（超长历史）、结构化输出失败重试
