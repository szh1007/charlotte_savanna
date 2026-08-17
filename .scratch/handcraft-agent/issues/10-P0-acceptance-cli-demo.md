# 10-P0-acceptance — CLI 演示（P0 验收线）

**What to build:** `python -m handcraft_agent.cli` 命令行入口：用真实 DeepSeek 跑通带工具问答（模型决策 → 工具执行 → 结果回填 → 最终答案），终端实时展示事件流（thinking/tool_call/tool_result/final）；checkpoint 中断续跑演示（Ctrl-C 中断 → 重跑从 checkpoint 恢复，不重复已完成动作）；配置切换 checkpoint 存储（InMemory/Redis/Postgres）。这是 P0 阶段整体验收线。

**Blocked by:** 04, 05, 07, 09

**Status:** ready-for-agent

- [ ] CLI 入口：带工具问答端到端跑通（真实 API）
- [ ] 终端事件流展示（四类事件 + reasoning）
- [ ] checkpoint 中断续跑演示：中断 → 恢复不重复已完成动作（#5）
- [ ] checkpoint 存储配置切换可演示（InMemory/Redis/Postgres，ADR-0002）
- [ ] `pytest tests/` 全绿（P0 验收：带工具的 agent loop 跑通 + 断点续跑 + mock 单测）
