# ADR-0003: LLM 调用层用 httpx 裸调 + openai SDK 双适配器

- 状态: accepted
- 日期: 2026-08-18（自 README 选型 0003 拆出；补充确认：接入模型为 `deepseek-v4-flash`，DeepSeek 官方 base，OpenAI 兼容协议）
- 考虑过的方案: 仅 openai SDK——拒绝，看不到协议细节；仅 httpx 裸调——拒绝，不贴近生产实际
- 后果: 两套 model 实现需保持行为一致（用同一组测试约束；真实 API 集成测试验证协议字段正确性）

同时提供两种 DeepSeek（OpenAI 兼容）接入：httpx 裸调（自己拼 `/chat/completions`、解析 tool_calls、处理 SSE 流式）和 openai SDK 适配器。httpx 裸调看清协议细节（契合「理解底层」教学目标），openai SDK 是生产实际做法，两者并列便于对比「SDK / 框架帮你藏了什么」。
