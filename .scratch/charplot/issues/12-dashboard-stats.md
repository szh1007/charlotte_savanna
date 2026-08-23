# 12 — 分析 Dashboard

**Status:** done

**Blocked by:** 02 — 账号体系与个人主页, 05 — 闯关答题与通关结算

**What to build:** 自建后台分析页（B 站粉风格图表）：掌握度矩阵（按知识点/章节正确率，薄弱点高亮）、学习活动统计（学习时长 / 通关数 / 活跃天数 / 连胜趋势）、易错点清单（易错分排序 + 复习优先级，与间隔复习同源）。数据从事实表（Attempt + 用户事件）聚合按需计算。

**Acceptance criteria:**
- [x] 掌握度矩阵：按知识点/章节正确率展示，薄弱点高亮，数据与 Attempt 一致
- [x] 活动统计：时长 / 通关数 / 活跃天数 / 连胜趋势图表展示
- [x] 易错清单：按易错分排序 + 复习优先级
- [x] 聚合数据来自事实表（Attempt + 用户事件），无额外埋点造假
- [x] 页面仅登录用户可见

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 12；PRD F-1/F-2/F-3；SPEC §10 / Q16

**实现摘要 (2026-08-24):**

**后端**（`app/charplot/dashboard.py` 新模块 + `views_api.py` + `urls_api.py`）：
- 三个聚合函数，全部直接查事实表（`CharplotAttempt` + `CharplotUserEvent`），无额外埋点：
  - `build_mastery_matrix(user)`：按旅程 → 章节 → 知识点聚合正确率。知识点归属 = `question.source_kp or level.knowledge_point`（与 `services.submit_answer` 易错分锚点一致，复习题计入来源知识点，掌握度与易错语义闭环）。正确率 < 60% 标记 `weak` 供前端高亮
  - `build_activity_stats(user, days=14)`：时长（Attempt.duration 求和）/ 通关数（LEVEL_CLEAR 事件计数，与 profile 统计同口径）/ 活跃天数（LOGIN 按日去重）/ 连胜（profile 当前值）/ 近 14 天每日 ANSWER+LEVEL_CLEAR 事件按日聚合（缺日补零，时间轴连续）
  - `build_weakpoint_list(user)`：全局 `error_score>0` 知识点，priority = error_score × (距复习天数 + 1) 与 `services._review_candidates` **同公式同 tie-break**（间隔复习同源，测试断言两函数排序一致）；priority_level 按清单排名三等分（高/中/低）；wrong_count 按来源归属聚合
- 数据隔离：mastery / weakpoints 均按 `user` 过滤（回归修复：初版 weakpoints 漏过滤会泄露其他用户易错数据，已加隔离测试）
- 时区修正：复习天数用 `timezone.localdate(kp.last_reviewed_at)` 而非 `.date()`（aware datetime 的 `.date()` 返回 UTC 日期，本地凌晨会偏 1 天；不向新代码扩散 `_review_candidates` 的既有缺陷）
- API：`GET /api/charplot/dashboard/mastery/` / `activity/` / `weakpoints/`（IsAuthenticated，未登录 403 与 profile 同惯例）

**前端**（`project/charplot/frontend/`）：
- `client.ts`：`MasteryPoint/Chapter/Journey`、`DailyActivity/ActivityStats`、`Weakpoint` 类型 + 三个请求函数
- `Dashboard.vue`（`/frontend-design` 技能落地，延续 B 站粉令牌）：页头动态摘要 → KPI 行（时长/通关/活跃天数/连胜+纪录，火焰呼吸动画与导航同族）→ 掌握度矩阵（旅程卡分组、章节汇总行、每知识点掌握度条配色分级 ≥80 主粉 / 60-80 浅紫 / <60 琥珀 + 「薄弱 · 易错分 N」红色系胶囊高亮）→ 易错清单（排名 + 优先级标签 优先/建议/顺带复习 + 答错次数）→ 近 14 天活跃柱状图（纯 CSS 零图表库，通关星标 ★，今日高亮）；空状态引导去学习
- 路由 `/dashboard`（requiresAuth）+ 导航「学习分析」按钮（登录态显示）

**验证**：Django 新增 16 用例（聚合与 Attempt/事件逐一比对 / 薄弱点阈值 / 章节汇总 / 复习题来源归属 / 近 14 天窗口与活跃标记 / 优先级公式与排序 / 与 `_review_candidates` 同源对比 / 分档 / 用户隔离 x3 / API 权限）——charplot 全套 246 测试全绿，ruff/codespell 干净；前端 `npm run build`（vue-tsc 类型检查）通过；Playwright 冒烟：登录可见、空状态引导、造数据后 KPI/矩阵/易错清单/趋势图渲染与事实表一致。为验证在 admin 账号造了 2 个测试旅程（闯关链路真实答题，可直接查看全效果，不需要可删）。
