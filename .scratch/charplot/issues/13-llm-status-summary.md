# 13 — LLM 状态总结

**Status:** ready-for-agent

**Blocked by:** 12 — 分析 Dashboard

**What to build:** 分析页「生成当前状态分析报告」：基于 12 的统计聚合结果，调 FastAPI LLM 接口生成文字版总结（强项 / 弱项 / 学习建议），前端展示报告卡片。

**Acceptance criteria:**
- [ ] 点击生成按钮触发 LLM 总结（FastAPI 接口），有加载状态
- [ ] 总结包含强项 / 弱项 / 建议三部分，基于统计聚合事实
- [ ] 生成结果可重复生成，失败有提示可重试
- [ ] 报告在前端优雅展示（markdown 渲染）

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 13；PRD F-4；SPEC §10 ④ / Q16
