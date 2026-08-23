# 08 — 真实题目生成 + 间隔复习 + Boss 标记

**Status:** done

**Blocked by:** 03 — 旅程创建与生成链路, 05 — 闯关答题与通关结算

**What to build:** 用真实 LLM 生成器替换 05 的 stub 题目：基于图谱知识点生成选择 / 判断 / 填空题目（含讲解与来源引用字段，来源在 07 完成后自动生效）；渐进生成策略（进入关卡时生成，预生成机制：学当前关时后台预生成下一关，复用任务系统）；间隔复习混入（按「易错分 × 时间衰减」排序 Top 20% 历史易错知识点，复用历史题目与讲解，无感融入不标注）；章节末尾 Boss 高难度标记关（混合题型）。

**Acceptance criteria:**
- [x] 三种题型真实生成，与知识点内容相关，讲解完整（来源字段预留）
- [x] 渐进生成：关卡按需生成，预生成机制生效（下一关提前生成，进入时无需等待）
- [x] 间隔复习：新关混入 Top 20% 易错点题目，复用历史题与讲解，无「复习」标注
- [x] 易错分规则生效（答错 +2 / 答对 -1，下限 0），时间衰减参与排序
- [x] Boss 标记关：章节末尾高难度混合题型，通关才可进入下一章
- [x] 生成任务失败可重试，进度 SSE 可见

**References:** DESIGN.md §7 步骤 08；PRD D-3/D-6、G-5；SPEC §7.3 幻觉防护 / §9 间隔复习 / 亮点2、亮点3

**实现摘要 (2026-08-23):**
- 出题链路: `POST /ai/levels/generate` → 任务系统(task_type=level-generation, preparing→generating→saving→done) → Django 内部端点抢占(claim, select_for_update + 10min 陈旧超时) → LLM 生成新题 → 落库(有 Attempt 关 update-in-place 保历史)
- 间隔复习: Django 侧 `_review_candidates`/`_pick_review_questions`, priority = error_score × (days+1), Top 20% 混入末尾, source_kp FK 路由易错分, last_reviewed_at 衰减闭环
- Boss 关: Level.level_type=boss + chapter FK, 8 题高难度混合, 章内常规关全清解锁本章 boss、boss 通关解锁下一章 (level_locked + LevelLockedError 400)
- 旧 stub 数据迁移: 已通关/进行中置 ready, 纯 pending 首次访问有机升级
