# 05 — 闯关答题与通关结算

**Status:** ready-for-human

**Blocked by:** 04 — 技能树地图

**What to build:** 完整的闯关答题闭环：进入关卡（5-8 题，由浅入深、简单题收尾；选择 / 判断 / 填空三种题型交互，填空模糊匹配判分；本票题目先由 stub 生成器产出）→ 提交答案立即判分 → 展示预生成讲解（含来源引用位）→ 答错扣心动值（5 心，扣完重开）→ 温和鼓励动画（非红色错误界面）→ 通关结算（XP / 学习币发放、连胜更新、技能树节点点亮）。答题记录（Attempt）与用户事件逐条落库。关卡进度持久化：`charplot_level` 进度字段（答到第几题 / 已通关）+ 剩余心动值，退出再进从断点续答；扣完 5 心 = 本关失败需重开（题目与心重置，历史 Attempt 保留不覆盖）。

**Acceptance criteria:**
- [x] 三种题型完整可答，填空模糊匹配（归一化）判分合理
- [x] 答对答错均展示讲解，答错扣心动值，5 心扣完本关重开
- [x] 答错展示温和鼓励动画，非错误红叉界面
- [x] 通关结算：XP / 学习币 / 连胜与规则一致，节点点亮
- [x] Attempt 与用户事件（答题/通关）逐条落库，可作为统计事实源
- [x] 关卡由浅入深 + 简单题收尾的题目顺序策略生效
- [x] 中途退出关卡再进：从进度字段断点续答，剩余心动值保留不重置
- [x] 5 心扣完重开：题目与心重置；已答 Attempt 保留不覆盖（历史事实，供掌握度分析）

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 05；PRD D-2/D-3/D-4/D-5、G-1/G-2/G-3；SPEC §6.3 / §8 / §9

## Comments

### 2026-08-23 — 实现完成 (Claude Code)

- 模型：`charplot_level` / `charplot_question` / `charplot_attempt`（migration 0004），进度字段 `current_index` + `hearts` + `cleared`
- stub 题目生成器在 Django 侧（`services.ensure_levels_for_journey`，幂等懒创建），确定性 6 题 = 选择×2 → 判断×2 → 填空×1 → 判断×1（简单收尾，PRD D-2）；Issue 08 真实生成时替换
- 判分：选择/判断精确匹配，填空 NFKC 归一化（全角→半角 + 去空白 + 小写）模糊匹配
- 结算规则（集中配置）：答对 +10 XP 即时、通关 +50 XP + 15 币、连胜按自然日（昨天学 +1 / 断连重计）、易错分答错 +2 / 答对 -1（下限 0）
- 关卡心为权威（`level.hearts`），`profile.hearts` 同步投影供导航显示；重开重置两者，Attempt 历史保留
- API：`GET journeys/{id}/levels/`（懒创建）、`GET levels/{id}/`（当前题，断点续答定位源）、`POST levels/{id}/answer/`（防重放：非当前题 400）、`POST levels/{id}/restart/`（已通关禁止）
- 前端：LevelList 重写（状态/进度/心 + query.kp 过滤）、QuizView 答题页（三种题型 + 温和反馈「没关系, 记住这个点就好」+ 心飞走动画 + 通关彩花结算 + 心扣完重开）、HeartsBar/Confetti/QuestionCard 组件、路由 `/journeys/:id/levels/:levelId`
- 技能树联动：`build_skill_tree` 从 Level 聚合（cleared_levels/total_levels/in_progress），通关自动点亮
- 验证：后端 126 测试全过（新增 test_quiz.py 41 个）+ ruff 全过 + vue-tsc 构建通过 + 浏览器 E2E 实测（答题/扣心/归一化/断点续答/结算/点亮/重开全链路）
- E2E 中发现并修复：通关后反馈区缺「查看结算」入口、`phase=failed` 无独立模板分支（显示"关卡不存在"）、通关/重开后导航徽章未刷新
