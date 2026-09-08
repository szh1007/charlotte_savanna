# 01-P0-1 — ChatModel 协议 + httpx 裸调适配器

**What to build:** 定义最小 ChatModel 协议（generate(messages, tools) -> ModelResponse），并用 httpx 裸调实现 DeepSeek（OpenAI 兼容，deepseek-v4-flash）接入——自己拼 /chat/completions 请求、解析响应（tool_calls 结构 / usage / finish_reason / reasoning_content）、处理 SSE 流式 delta 累积。协议隔离「模型与 runtime 解耦」，为 MockLLM 测试与 SDK 适配器提供同一接口。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] ChatModel 协议定义完成：generate 签名、ModelResponse（文本 or ToolCall 列表）、FinishReason（stop/tool_calls/length/content_filter）、reasoning_content 字段兼容分支（有/无推理字段均可解析）
- [ ] httpx 适配器完成：非流式请求正确解析 tool_calls/usage/finish_reason；流式请求 delta 累积为完整响应
- [ ] 协议字段解析测试通过（mock HTTP）：tool_calls 结构、reasoning 分离、finish_reason 各取值（#11/#68）
- [ ] RUN_INTEGRATION=1 真实 API 集成测试可跑：协议字段正确性验证（#61 录制回放样本来源）
- [ ] 错误语义：非 2xx 映射为明确异常（供 retry 判断瞬态/永久）
