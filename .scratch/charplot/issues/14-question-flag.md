# 14 — 题目反馈标记

**Status:** ready-for-agent

**Blocked by:** 05 — 闯关答题与通关结算

**What to build:** 答题页每题提供「题目有问题」反馈按钮，点击后标记落库（题目 + 用户 + 原因可选），管理员侧 / Dashboard 可见标记列表，作为内容质量信号（幻觉防护第三层）。

**Acceptance criteria:**
- [ ] 答题页每题有「题目有问题」入口，点击后落库并给出已反馈提示
- [ ] 标记记录题目 / 用户 / 时间 / 可选原因
- [ ] 标记在管理侧或 Dashboard 可见（列表 + 计数）
- [ ] 同一用户对同一题重复标记去重

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 14；PRD D-7；SPEC §7.3 ③ / Q8
