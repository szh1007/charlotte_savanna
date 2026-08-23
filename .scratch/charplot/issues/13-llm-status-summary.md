# 13 — LLM 状态总结

**Status:** done

**Blocked by:** 12 — 分析 Dashboard

**What to build:** 分析页「生成当前状态分析报告」：基于 12 的统计聚合结果，调 FastAPI LLM 接口生成文字版总结（强项 / 弱项 / 学习建议），前端展示报告卡片。

**Acceptance criteria:**
- [x] 点击生成按钮触发 LLM 总结（FastAPI 接口），有加载状态
- [x] 总结包含强项 / 弱项 / 建议三部分，基于统计聚合事实
- [x] 生成结果可重复生成，失败有提示可重试
- [x] 报告在前端优雅展示（markdown 渲染）

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 13；PRD F-4；SPEC §10 ④ / Q16

**实现摘要 (2026-08-24):**

遵循 DESIGN.md §4.2 预留契约 `POST /ai/report/summary` `{user_id}` → `{summary}`（同步单次 LLM 生成，不落库、可重复生成，复用既有 `llm.get_chat_model()` DeepSeek 单例）。

**后端 — Django**（`app/charplot/`）：
- 新增内部端点 `GET /api/charplot/users/{id}/status-summary-input/`（`StatusSummaryInputView`，X-Internal-Token 认证）：透传三块聚合（`build_mastery_matrix` / `build_activity_stats` / `build_weakpoint_list`，与 Dashboard 三个用户端点同构），按 user_id 查询实现用户隔离，用户不存在 404。聚合数据从 Django 侧权威获取，前端无法伪造篡改

**后端 — FastAPI**（`project/charplot/`）：
- `django_client.fetch_status_summary_input(user_id)`：内部端点客户端，404 抛 `UserNotFoundError`（转 404）、网络/4xx/5xx 抛 `RuntimeError`（转 502）
- `prompt/status_summary.py`：`STATUS_SUMMARY_SYSTEM_PROMPT`（严格三段 markdown 标题：`## 强项` / `## 弱项` / `## 学习建议`，鼓励性温和语气，只基于事实）+ `build_status_summary_prompt`（**prompt 裁剪**：活动汇总 + 章节级正确率 + 薄弱知识点标题 + 易错清单进入，daily 14 天明细与知识点级明细不进入，避免稀释 LLM 关注点）
- 错误语义：用户不存在 404 / 聚合获取失败 502 / LLM 未配置 503 / LLM 调用失败 502（均可在修复后重试）

**前端**（`project/charplot/frontend/`，`/frontend-design` 技能落地）：
- `api/client.ts`：`generateStatusSummary(userId)` 请求函数
- `components/MarkdownText.vue`：**轻量 markdown 渲染器（零依赖）** — 先全量 HTML 转义再白名单语法替换（## / ### 标题、**粗体**、- / 1. 列表、段落），杜绝 XSS；配色由调用方 :deep() 覆盖，组件保持中性
- `Dashboard.vue`：「AI 学习总结」面板（KPI 行之后、掌握度矩阵之前 — 体检报告"医生总结在前"）：主粉按钮（loading「正在分析…」→ 成功变「重新生成」）/ 报告卡片签名元素 = 顶部粉→浅紫渐变细条（与趋势柱渐变同族）+ h3 标题粉左色条 / 空数据（从未答题）按钮禁用 + 引导先去闯关 / 失败 ElMessage 提示 + 按钮恢复可重试

**验证**：
- FastAPI 新增 8 用例（契约 / 缺 user_id 422 / 404 / 502 x2 / 503 / prompt 裁剪含事实不含明细 / 空数据处理）——charplot FastAPI 全套 78 测试全绿
- Django 新增 5 用例（token 认证 x2 / 404 / 三块聚合与聚合函数同构 / 用户隔离）——charplot Django 全套 252 测试全绿
- ruff check + format / pre-commit 全过；前端 `npm run build`（vue-tsc 类型检查）通过
- Playwright 真实冒烟（真实 DeepSeek 调用）：smoke 账号答题数据 → 点生成 → loading → 三段 markdown 报告渲染（内容与聚合事实一致：2 关 / 1 天连胜 / 4-7 题 / 易错点清单）→ 重新生成成功 → 停 FastAPI 点生成失败（占位恢复 + 按钮可重试）→ 恢复 FastAPI 重试成功。冒烟临时用户已删除
