# 05-P0-4 — 流式事件状态机 + hook 注册表骨架

**What to build:** StreamEvent 事件总线：thinking / tool_call / tool_result / final 四类事件 + reasoning 流式增量独立事件（#11，展示但不存历史、不入后续上下文）；事件带 seq 序号支持断点续拉。hook 注册表骨架（before_turn / after_turn / on_model_call / on_tool_executed / on_event）P0 落地，空注册零成本——P2 模块（memory/cost/observability）经此挂载（ADR-0007），核心零 import P2。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 四类事件类型定义 + reasoning 增量事件（依赖 ModelResponse 的 reasoning 结构）
- [ ] 事件状态机：合法转换（thinking → tool_call → tool_result → final）不可乱序
- [ ] 每事件带 seq 序号
- [ ] hook 注册表骨架：5 个 hook 点 + 注册 API，空注册零开销（ADR-0007 扩展点）
- [ ] reasoning 事件与历史分离验证（#11）
- [ ] delta 与 final 的权威性约定：delta 仅作渐进预览，`final.content` 为权威值（前端收到即覆盖缓冲）——CONDENSE 丢弃的截断前缀已按 delta 推送过，只累加会显示作废内容（#10）
- [ ] thinking / final 的文本边界：非终止轮（工具轮等）的 assistant 正文归 thinking，终止轮才归 final；该边界 loop 侧已算出（AgentLoop 中「工具轮打断拼合链」的 content_parts.clear 处即同一判断），直接复用不重新推导
- [ ] guard 刹车（MAX_TURNS / TIME_LIMIT / TRUNCATION_LIMIT，此时 LoopResult.content=None）的事件契约：走 error 事件还是带降级说明的 final，需在 03-api.md §2 定案
