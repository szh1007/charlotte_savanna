# Bugfix 0006 — 第二轮 UI 反馈 (信息刷新 / 确认弹窗 / 过期视觉 / 清晰度排序)

> 记录日期: 2026-08-20 | 状态: 已实施 | 涉及文件: `backend/events.py`、`frontend/src/components/ConfirmDialog.vue`（新）、`frontend/src/components/TaskPanel.vue`、`frontend/src/components/ResolveResult.vue`、`frontend/src/views/Home.vue`、`tests/test_events.py`、`docs/DESIGN.md`、`README.md`

## 问题现象与修复

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 1 | 点击开始下载后任务详细信息（标题/封面）不展示，刷新页面才恢复 | 竞态: 后端先广播 resolving 事件后返回 POST 响应，SSE 先到时防抖刷新已把后端 resolving 快照（resolve 未完成，title/cover 为空）拉回列表；本地 unshift 去重时该 id 已存在 → 跳过 → 卡片长期占位 | ① Home `handleDownload`: 同 id 卡已存在时用解析结果**补全缺失字段**（title/cover/duration/site/formats）而非跳过; ② 后端 `task_event` 携带 title/cover（resolve 完成后任意事件可补全卡片，覆盖跨标签页场景） |
| 2 | 清除记录 / 清除所有过期记录二次确认用浏览器原生 confirm | 项目零 UI 库时图省事用了 `window.confirm` | 新建 `ConfirmDialog.vue`（Teleport 遮罩弹窗，Esc/遮罩取消，危险操作红色确认按钮）; 单条与批量清除均走自定义弹窗; Home 的两处 `window.alert` 失败提示一并替换（同一组件提示模式, 全站无原生弹窗） |
| 3 | 旧的「已过期」字样与新的重复 | ttl 区新增「已过期」span + 徽章 statusText 已按视图判定显示「已过期」，两处并存 | 删除 ttl 区的「已过期」span，仅保留徽章一处（statusText 已处理倒计时归零的本地过期显示） |
| 4 | 倒计时归零后卡片背景未变红 | 卡片样式只按 `task.status` 判定，本地归零的 completed 卡未触发；徽章色同样按原始 status（completed 绿） | 卡片 `:class="task-card--expired"`（isExpiredView 判定）+ 红色底色/边框样式; 新增 `statusTone(task)` 按视图判定取徽章色（归零即红） |
| 5 | 下载完成后进度条仍显示 100% | 进度条 `v-if` 包含 completed | 进度条仅 `downloading` 显示，完成后由倒计时/操作按钮替代信息区 |
| 6 | 清晰度下拉低档在上、默认选中逻辑与展示不一致感 | 后端按高度升序排列，下拉原样渲染 | `formatsDesc` 倒序渲染（高清晰度在第一位）；默认选中逻辑不变（倒序找第一个未锁定档 = 最高可用档，免费取最高免费档） |

## 关键实现

1. **`ConfirmDialog.vue`**（新组件）: `visible/title/message/confirmText/danger/hideCancel` props，确认模式双按钮、提示模式（hide-cancel）仅「知道了」；Teleport 到 body，遮罩点击与 Esc 关闭。
2. **TaskPanel.vue**: `confirmState` 驱动弹窗（kind: clear 单条 / clear-expired 批量），单条未过期弹确认（含任务名），批量弹确认（含过期数量）；删除 window.confirm。
3. **Home.vue**: `errorDialog` 复用 ConfirmDialog 提示模式替换 window.alert；`handleDownload` 竞态补全（见上表 #1）。
4. **后端 events.py**: `task_event` 增 `title`（空串兜底）/`cover` 字段，契约同步 DESIGN.md / README.md。
5. **ResolveResult.vue**: `formatsDesc` computed 倒序渲染。

## 验证

- `tests/test_events.py` completed 事件补充 title/cover 断言（`测试视频标题` / `https://example.com/cover.jpg`）
- 全量测试通过（75 passed）；ruff check + format 通过；前端 `npm run build` 通过
- 竞态场景（#1）验证方式: SSE 事件先于 POST 响应到达时，本地卡由解析结果补全，无占位残留

## 未采纳方案

| 方案 | 说明 | 评价 |
|------|------|------|
| 仅前端补全、后端事件不加 title/cover | 改动更小，但跨标签页场景（事件先到、列表刷新时 resolve 未完成）仍会信息缺失 | 后端事件本就携带 Task 快照，加两字段成本低且根治 |
| 引入 UI 组件库做弹窗 | 项目零 UI 库（手写样式保独特风格），为一个弹窗引入依赖过重 | 手写 60 行组件足够 |
