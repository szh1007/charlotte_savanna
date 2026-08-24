# 14 — 题目反馈标记

**Status:** done

**Blocked by:** 05 — 闯关答题与通关结算

**What to build:** 答题页每题提供「题目有问题」反馈按钮，点击后标记落库（题目 + 用户 + 原因可选），管理员侧 / Dashboard 可见标记列表，作为内容质量信号（幻觉防护第三层）。

**Acceptance criteria:**
- [x] 答题页每题有「题目有问题」入口，点击后落库并给出已反馈提示
- [x] 标记记录题目 / 用户 / 时间 / 可选原因
- [x] 标记在管理侧或 Dashboard 可见（列表 + 计数）
- [x] 同一用户对同一题重复标记去重

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 14；PRD D-7；SPEC §7.3 ③ / Q8

## Comments

### 2026-08-24 — 实现完成 (Claude Code)

- 模型：`charplot_question_flag`（migration 0009），题目/用户/可选原因（预设 choices：答案有误/内容有误/讲解有误/其他）/时间；`unique(question, user)` 约束去重（验收标准 4）
- 服务：`flag_question` get_or_create 幂等（重复标记返回 created=False 且不覆盖原 reason）
- API：`POST /api/charplot/questions/{id}/flag/`（DESIGN §4.1 契约），题目归属校验与关卡一致（仅本人旅程可标记，404 不泄露存在性），reason 非法值 400
- 序列化：`QuestionBrief.flagged` 按当前用户计算（LevelDetail 透传 request context），重进答题页恢复「已反馈」状态
- 管理侧可见（验收标准 3）：admin 注册 `CharplotQuestionFlagAdmin`（列表 + reason 过滤 + 题目/用户搜索 + 只读）+ `CharplotQuestionAdmin` 加 flag_count 计数列（annotate 防 N+1）
- 前端：QuestionCard 底部低调「题目有问题? 反馈一下」入口（答前答后均可反馈，讲解有误也是反馈点）→ inline 原因 chips（可跳过直提）→ 提交后 ElMessage 提示 + 按钮变「已反馈, 感谢你的帮助 ✓」禁用；QuizView 集成 flag 提交与初始状态透传
- 验证：新增 test_question_flag.py 13 个测试（落库/去重/跨用户/原因可选/403/404/非法原因/flagged 持久化与用户隔离），全量 265 测试通过 + ruff 全过 + vue-tsc + vite build 通过 + 浏览器 E2E 实测（登录 → 进入关卡 → 反馈 → 已反馈态 → 刷新恢复 → 落库/计数核对）
