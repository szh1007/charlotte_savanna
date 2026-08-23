# 03 — 旅程创建与生成链路

**Status:** done

**Blocked by:** 01 — 三端骨架与健康检查, 02 — 账号体系与个人主页

**What to build:** 用户输入想学的知识（纯文本 / 文件 / 网页链接）创建学习旅程：FastAPI 侧启动生成任务（本票先以 stub 管道产出示例图谱），SSE 阶段化推进度（解析 → 分析 → 搜索 → 解构 → 完成，失败可重试），完成后图谱落库（Journey / Chapter / KnowledgePoint 含依赖边）。旅程列表页展示进行中 / 已通关。**本票定义全链路数据契约**——后续真实管道（07）与题目生成（08）均遵循此契约。

**Acceptance criteria:**
- [x] 文本 / 文件 / 网页链接三种输入均可创建旅程
- [x] SSE 阶段进度可见（五阶段），失败可重试
- [x] stub 图谱按契约落库：Journey → Chapter → KnowledgePoint（前置依赖边）
- [x] 旅程列表区分进行中 / 已通关，可回到未完成旅程
- [x] 数据契约文档化（图谱 JSON 结构），真实管道可无缝替换 stub

**实现记录（2026-08-22）:** 全链路闭环 —— Django（journeys CRUD + 内部落库端点 X-Internal-Token 认证 + 先删后建幂等）、FastAPI（任务系统 Redis 状态 + LIST 事件 + SSE Last-Event-ID 增量恢复 + stub 管道）、前端（Home 输入区三形态 + JourneyDetail SSE 进度/图谱/重试）、契约文档 `project/charplot/docs/CONTRACT.md`。测试：Django 73 + FastAPI 17 全绿；手动 E2E 覆盖三输入 / SSE 五阶段 / 失败重试（停 Django → error → 恢复 → 重试 done）/ 分组展示。

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 03；PRD B-1/B-2/B-3；SPEC §6.2 / §8
