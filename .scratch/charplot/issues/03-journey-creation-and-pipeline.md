# 03 — 旅程创建与生成链路

**Status:** ready-for-agent

**Blocked by:** 01 — 三端骨架与健康检查, 02 — 账号体系与个人主页

**What to build:** 用户输入想学的知识（纯文本 / 文件 / 网页链接）创建学习旅程：FastAPI 侧启动生成任务（本票先以 stub 管道产出示例图谱），SSE 阶段化推进度（解析 → 分析 → 搜索 → 解构 → 完成，失败可重试），完成后图谱落库（Journey / Chapter / KnowledgePoint 含依赖边）。旅程列表页展示进行中 / 已通关。**本票定义全链路数据契约**——后续真实管道（07）与题目生成（08）均遵循此契约。

**Acceptance criteria:**
- [ ] 文本 / 文件 / 网页链接三种输入均可创建旅程
- [ ] SSE 阶段进度可见（五阶段），失败可重试
- [ ] stub 图谱按契约落库：Journey → Chapter → KnowledgePoint（前置依赖边）
- [ ] 旅程列表区分进行中 / 已通关，可回到未完成旅程
- [ ] 数据契约文档化（图谱 JSON 结构），真实管道可无缝替换 stub

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 03；PRD B-1/B-2/B-3；SPEC §6.2 / §8
