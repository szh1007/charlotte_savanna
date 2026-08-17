# 28-P2-4 — cost 插件（成本追踪 / 分级路由 / 语义缓存 / batch）

**What to build:** 成本插件（P2）：CostTracking 按 task（而非 token）归因 + 预算硬上限 + 告警（#34）；ModelRouter 模型分级路由（简单步骤 cheap 模型、难推理 strong 模型 #36）；SemanticCache 语义缓存（高频相似问题命中，防穿透/击穿/雪崩三大风险 #36/#37）；BatchAPI 批量异步 API（打包请求降本约 50%，适合离线评估 #37）。经 hook（on_model_call 计量 / after_turn 归因）与 SPI（ModelRouter/SemanticCache）挂载（ADR-0007）。

**Blocked by:** 05, 21

**Status:** ready-for-agent

- [ ] 成本追踪：按 task 归因 + 预算硬上限 + 告警（#34）
- [ ] 分级路由：ModelRouter SPI 实现（#36）
- [ ] 语义缓存：命中/未命中 + 穿透/击穿/雪崩防护（#36/#37）
- [ ] Batch API：打包请求（#37）
- [ ] 插件经配置注册挂载，核心零 import（ADR-0007）
