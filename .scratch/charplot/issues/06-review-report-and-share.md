# 06 — 复盘报告与公开分享

**Status:** ready-for-human

**Blocked by:** 05 — 闯关答题与通关结算

**What to build:** 学习旅程通关后生成复盘报告（知识总结 + 答题表现），报告页可查看；一键生成公开只读分享链接（slug URL + OG 社交卡片），未登录用户可直接访问分享页（只读防篡改）。

**Acceptance criteria:**
- [x] 旅程全部关卡通关后自动生成复盘报告（知识总结 + 答题统计）
- [x] 报告页展示完整，数据与 Attempt 一致
- [x] 生成分享链接：slug URL，未登录可访问，页面只读
- [x] OG 社交卡片生效（分享到社交平台显示标题/摘要/缩略图）
- [x] 链接可重复访问，不可被篡改内容

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 06；PRD E-1/E-2；SPEC §11 / §8 ReviewReport

## Comments

### 2026-08-23 实施完成 (Claude)

后端:
- 新表 `charplot_review_report` (migration 0005): journey 1:1 + slug (secrets 随机 12 位, 去易混淆字符) + 知识总结/统计快照 + OG 文本/图
- `services.py`: `create_review_report` (幂等) / `build_report_stats` (与 Attempt 逐条一致) / `build_knowledge_summary` / Pillow 绘制 1200x630 OG 卡片 (B站粉渐变, 中文字体候选加载, 失败不阻塞)
- 通关结算 `_settle_level_clear` 在旅程全关通关时同事务自动生成报告
- API `GET /api/charplot/journeys/{id}/report/` (本人可见, 未通关 404, 返回 share_url)
- 公开分享页 `GET /r/{slug}/` (views_html.py + urls_html.py, 根路由挂载): 匿名可访问, 服务端渲染 OG meta, 只读无写端点, 未知 slug 404
- 测试 `tests/test_review_report.py` 18 个: 生成/幂等/统计一致性/重开历史计入/slug/OG 图文件/API 归属/分享页匿名+OG+404+只读

前端:
- `ReportView.vue` + 路由 `/journeys/:id/report`: 正确率环形徽章 hero (签名元素), 关卡表现, 知识总结, 分享卡片 (复制链接 + OG 说明)
- 入口: QuizView 结算页 `journey_cleared` 时主按钮跳报告; JourneyDetail 已通关旅程显示报告按钮
- `vite.config.ts` 增加 `/r` 代理到 Django (dev 下分享链接可直访)

验收对照: 通关自动生成 ✅ / 报告数据与 Attempt 一致 (测试断言) ✅ / slug URL 匿名访问只读 ✅ / OG 卡片 (标题/摘要/缩略图, 图文件生成验证) ✅ / 可重复访问 + 快照不可变 ✅

运行: `python manage.py test app.charplot` 144 全绿; ruff check/format 通过; 前端 build 通过; dev 数据库端到端冒烟通过后已清理。
