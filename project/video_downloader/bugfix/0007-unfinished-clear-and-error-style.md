# Bugfix 0007 — 第三轮 UI 反馈 (任意状态清除 / 批量清除未完成 / 错误提示美化 / 档位文案)

> 记录日期: 2026-08-20 | 状态: 已实施 | 涉及文件: `backend/task_manager.py`、`backend/cleaner.py`、`backend/routers/downloads.py`、`backend/downloader.py`、`frontend/src/components/ErrorAlert.vue`（新）、`frontend/src/components/TaskPanel.vue`、`frontend/src/components/HeroSection.vue`、`frontend/src/components/ResolveResult.vue`、`frontend/src/components/MemberSection.vue`、`frontend/src/views/Home.vue`、`frontend/src/api/client.js`、`tests/test_clear_records.py`、`tests/test_resolve.py`、`tests/test_downloader.py`、`tests/test_downloads.py`、`docs/DESIGN.md`、`README.md`

## 问题现象与修复

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 1 | 非已完成任务无「清除记录」按钮（失败记录没法删，看着难看） | 清除按钮 `v-if` 仅 completed/expired；后端 DELETE 对运行中任务返回 409 | ① 前端: 所有任务卡均显示「清除记录」; ② 后端: DELETE 放开任意状态 — 下载中任务经 `cancel_event` 取消信号中断引擎（progress hook 抛 `DownloadError`，yt-dlp 官方取消方式，临时文件自清），任务 `file_path` 未回填无残留 |
| 2 | 「清除所有已过期记录」无法清理失败/排队记录 | purge 范围仅 expired + 超时 completed | 接口改 `POST /api/tasks/purge-unfinished`：清除全部未完成任务（expired / failed / 排队中 / 下载中取消 / 已超 TTL 的 completed），仅保留可交付的 completed；前端按钮与弹窗文案同步「清除所有未完成记录」 |
| 3 | 解析/下载/下载过程失败等红字报错不美观 | 各处为裸红字文本（无底色/边框/图标） | 新建 `ErrorAlert.vue` 统一提示条（⚠️ 图标 + 浅红底 + 描边 + 入场动画），替换 4 处：Hero 解析错误、ResolveResult 创建下载错误、TaskPanel 下载失败/过期提示、MemberSection 密钥错误 |
| 4 | 档位文案「最佳画质 (1080p)」用括号 | 后端 label 拼装 `f"最佳画质 ({height}p)"` | 改为 `f"最佳画质 - {height}p"`（用户指定格式），测试断言 3 处同步 |

## 关键实现

1. **取消下载机制**（task_manager.py）:
   - `Task.cancel_event` 字段: 清除记录时置位
   - `remove_task`: 下载中任务置取消信号后 pop + 广播 removed
   - `_progress_hook`: 检查取消信号 → 抛 `DownloadError("已取消")` 中断引擎
   - `update_status` 防御: 任务已被移除时跳过更新/广播（worker 收尾落点）
   - `_run_download(task_id, is_member)`: is_member 派发时捕获, 任务被移除时直接退出, 槽位始终按派发身份释放（防泄漏）
2. **purge_unfinished**（cleaner.py + 路由）: 遍历 `list_all()`，仅保留「completed 且未超 TTL」，其余一律 `remove_task`（下载中自动取消）+ 孤儿文件清理逻辑不变。
3. **ErrorAlert.vue**: 空消息不渲染（调用方无需 v-if）；间距由父组件 class 作用于根节点（Hero 14px / Member 12px）。
4. **TaskPanel**: `isFinishedView(task)`（completed 且未过期）驱动批量按钮显隐与清除确认文案；failed/expired 直接清除（无文件），进行中弹「清除将取消下载」确认。

## 验证

- 后端全量测试 79 passed（新增 5 个: 下载中取消 / 排队中取消 / 失败记录删除 / purge 覆盖失败+排队 / purge 取消下载中；删 409 拒绝测试）
- ruff check + format 通过；前端 `npm run build` 通过

## 未采纳方案

| 方案 | 说明 | 评价 |
|------|------|------|
| 仅放开 failed 删除、下载中仍 409 | 改动最小，但排队/下载中任务点击清除必被后端拒绝，交互割裂（用户明确要求「只要不是已完成都要显示」） | 取消机制成本低（hook 抛异常是 yt-dlp 官方方式），语义完整 |
| 保留 purge-expired 并新增 purge-unfinished | 两个批量接口并存，前端只用新的，旧的成为死代码 | 合并为一个接口，超集语义（expired 本就是未完成） |
