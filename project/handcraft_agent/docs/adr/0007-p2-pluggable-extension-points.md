# ADR-0007: P2 模块可插拔化——轻量扩展点（事件总线 + hook + SPI）

- 状态: accepted
- 日期: 2026-08-18
- 考虑过的方案: 完整插件框架（registry + 元数据声明 + 生命周期 + 依赖解析）——拒绝，8 个 P2 模块、单人维护，过度设计（YAGNI）；物理独立包（P2 拆成独立包）——拒绝，解耦最彻底但共享基础设施（日志、序列化、连接池）需重复实现，最终仍需回头改核心
- 后果: P2 模块只依赖核心公开接口，核心零 import P2；hook 注册表骨架在 P0 落地（空注册零成本），P2 只填实现；P0/P1 验收不受 P2 进度影响

## 上下文

项目按 P0-P2 三阶段实施 70 个难点。P0/P1 组成框架核心 + 客服 demo 闭环（45 个难点），P2 的 8 个模块（multiagent / mcp / skills / memory / cost / eval / observability / 上下文工程）属于增量能力，不得影响核心闭环的稳定性与验收进度。

## 决策

核心库（`handcraft_agent/`）只定义三类轻量扩展机制：

1. **事件总线**：StreamEvent 四类事件（thinking / tool_call / tool_result / final）本就是 P0 的流式输出通道，P2 模块（observability、cost）订阅同一条事件流
2. **hook 点**：`before_turn` / `after_turn` / `on_model_call` / `on_tool_executed` 等生命周期回调，注册表骨架 P0 落地（空实现零成本），P2 模块（memory、guard 扩展）按需挂载
3. **SPI**：可替换接口——`ChatModel` / `CheckpointSaver` / `EmbeddingProvider` / `ModelRouter` / `SemanticCache`，P2 提供新实现类经配置注册

P2 模块作为同仓库独立子包（`handcraft_agent/plugins/` 平级目录），**配置注册 + 惰性 import** 启用，核心代码不出现对 P2 的 import。

## 决策记录

P2 可插拔形态：核心提供三类扩展点，P2 模块按需挂载，实现与核心闭环互不影响（2026-08-18 访谈 Q1/Q2 确认）。
