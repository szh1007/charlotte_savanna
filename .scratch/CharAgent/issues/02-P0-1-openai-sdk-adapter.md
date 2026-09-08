# 02-P0-1 — openai SDK 适配器 + 双适配器一致性契约测试

**What to build:** 用 openai SDK 实现同一 ChatModel 协议（同 DeepSeek base_url），与 httpx 裸调实现并列。同一组契约测试约束两个实现：对同一输入产生等价的 ModelResponse（tool_calls、reasoning、finish_reason、usage 全字段等价），演示「SDK 帮你藏了什么」（ADR-0003）。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] openai SDK 适配器实现 ChatModel 协议（非流式 + 流式）
- [ ] 契约测试：httpx 裸调 vs openai SDK 对同一 mock 输入产生等价 ModelResponse（#61 契约测试）
- [ ] reasoning_content 两实现均正确分离（有/无推理字段两分支）
- [ ] 流式 delta 累积结果与非流式一致
