# 05-P0-4 — 流式事件状态机 + hook 注册表骨架

**What to build:** StreamEvent 事件总线：thinking / tool_call / tool_result / final 四类事件 + reasoning 流式增量独立事件（#11，展示但不存历史、不入后续上下文）；事件带 seq 序号支持断点续拉。hook 注册表骨架（before_turn / after_turn / on_model_call / on_tool_executed / on_event）P0 落地，空注册零成本——P2 模块（memory/cost/observability）经此挂载（ADR-0007），核心零 import P2。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 四类事件类型定义 + reasoning 增量事件（依赖 ModelResponse 的 reasoning 结构）
- [ ] 事件状态机：合法转换（thinking → tool_call → tool_result → final）不可乱序
- [ ] 每事件带 seq 序号
- [ ] hook 注册表骨架：5 个 hook 点 + 注册 API，空注册零开销（ADR-0007 扩展点）
- [ ] reasoning 事件与历史分离验证（#11）
