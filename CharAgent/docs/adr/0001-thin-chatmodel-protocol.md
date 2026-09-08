# ADR-0001: 薄 ChatModel 协议（不做多 provider 抽象）

- 状态: accepted
- 日期: 2026-08-18（自 README 选型 0001 拆出）
- 考虑过的方案: LangChain 式胶水层（`init_chat_model` 统一 OpenAI/DeepSeek/Qwen）——拒绝，过度抽象，偏离「手写 loop、理解底层」主线；裸调不抽象——拒绝，讲不出解耦设计
- 后果: 接新模型需新增实现类（而非改配置）；协议必须足够薄，否则退化成胶水层

定义最小 `ChatModel` 协议（`generate(messages, tools) -> ModelResponse`），P0 只实现 DeepSeek 一种模型。协议隔离已足以演示「模型与 runtime 解耦」。
