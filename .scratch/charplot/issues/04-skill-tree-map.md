# 04 — 技能树地图

**Status:** done

**Blocked by:** 03 — 旅程创建与生成链路

**What to build:** 学习旅程的闯关地图页：知识图谱以技能树形式可视化（知识点节点 + 前置依赖边），节点显示通关点亮状态（大知识点拆多关时进度合并显示），节点可点击进入关卡。B 站粉主题渲染。图渲染库在此票完成选型（relation-graph / vue-flow / AntV X6 择一）。

**Acceptance criteria:**
- [x] 图谱正确渲染：节点（知识点）与依赖边布局正确，DAG 结构可见（vue-flow + dagre 分层，浏览器验证 5 节点 + 5 边）
- [x] 节点点亮状态正确（已通关 / 进行中 / 未解锁，依赖未满足时锁定；状态计算后端 `build_skill_tree`，测试覆盖三分支）
- [x] 大知识点多关卡时节点进度合并显示（如 2/3；`cleared_levels/total_levels` 契约字段就位，Issue 05 数据流入后徽章显示）
- [x] 点击可解锁节点进入关卡入口（`/journeys/{id}/levels?kp=<id>` 占位页，锁定节点点击拦截）
- [x] 图渲染库选型落地（vue-flow + dagre，见 adr/0004），动画流畅（点亮动效：渐变 + 光晕 + 弹起动画已验证生效）

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 04；PRD D-1；SPEC §9 技能树 / §4 图渲染库
