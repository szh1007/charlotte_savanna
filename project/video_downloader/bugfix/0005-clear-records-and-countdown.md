# Bugfix 0005 — 任务序号从 #2 开始 + 交付倒计时与清除记录功能

> 记录日期: 2026-08-20 | 状态: 已实施 | 涉及文件: `backend/config.py`、`backend/schemas.py`、`backend/events.py`、`backend/task_manager.py`、`backend/cleaner.py`、`backend/routers/downloads.py`、`frontend/src/api/client.js`、`frontend/src/views/Home.vue`、`frontend/src/components/TaskPanel.vue`、`tests/test_clear_records.py`、`docs/DESIGN.md`

## 问题现象

1. **任务占位序号从 #2 开始**：标题缺失时任务卡显示「下载任务 #2」——占位序号直接用了全局自增 `task_id`，而首个 resolve 任务已占用 id=1，第一个下载任务必然从 #2 起。
2. **任务列表无过期提示**：下载完成后用户不知道交付链接还剩多久过期，也无主动清除记录的入口——过期记录只能等 60s 周期清理，之后仍永久残留在内存与前端列表。
3. **封面/标题不显示**（关联确认）：此现象根因已由 bugfix/0001（图床 Referer 防盗链 + https 归一化）与 bugfix/0004（unshift 去重 + 合并式刷新）修复，本次确认无需再改。

## 后端遗漏盘点（用户询问「还有其他需要清除的东西吗」）

| 待清理项 | 处理前状态 | 本次处理 |
|----------|-----------|----------|
| TTL 周期清理（cleaner 线程） | ✅ 已有（60s 扫描删文件 + 标 expired） | 保留 |
| **expired 任务内存残留** | ❌ 文件删了但任务永驻 `_tasks` 字典 | 新增 `purge_expired` 批量移除 |
| **孤儿文件**（手动清除时删除失败 / 崩溃残留 `.part`） | ❌ 无人清理 | `purge_expired` 顺带清理（无任务引用且超 24h 的文件） |
| SSE 订阅 | ✅ 断开自动 unsubscribe | 无需处理 |

## 如何修复

### 后端

1. **`config.delivery_ttl(is_member)`**：TTL 按身份计算收归单一来源，cleaner 过期判定、任务序列化、SSE 事件共用，避免判定漂移。
2. **`TaskOut` 新增 `format_id` / `expires_at`**：`format_id` 供前端标题旁清晰度标注；`expires_at = completed_at + 身份 TTL`（仅 completed 携带）。SSE `task_event` 同步增加 `expires_at`（completed 时携带）。
3. **`TaskManager.remove_task(task_id)`**：从 `_tasks` 移除并广播 `removed` 事件（`{task_id, status: "removed"}`，非状态机状态——移除即不存在）；新增 `list_all()` 供批量清除全量扫描。
4. **`cleaner.purge_expired()`**：一键清除全部过期记录——expired 任务 + 已超 TTL 的 completed 任务（不等 60s 周期，立即生效），删文件 + 移除任务；顺带清理 DOWNLOADS_DIR 孤儿文件（无任务引用且超过 24h 未修改，mtime 门槛保护正在下载的文件）。
5. **路由**：
   - `DELETE /api/tasks/{id}`：清除单条记录——删文件 + 移除任务 + 广播 removed；运行中任务（排队/下载中）409 拒绝；文件删除失败（占用等）409 提示重试；不存在 404。
   - `POST /api/tasks/purge-expired`：批量清除，返回 `{removed: int[]}`。

### 前端

1. **占位序号**：`下载任务 #${index + 1}`（列表顺序）替代 `task_id`，从 #1 开始。
2. **标题清晰度**：标题后追加选定档位 label（如 `720p MP4` / `最佳画质 (1080p)`），数据源 `format_id` + `formats`（Home 本地构造卡补传，SSE/刷新合并保留）。
3. **交付倒计时**：completed 卡右侧实时显示 `hh:mm:ss`（1s tick，等宽字体防跳动），剩余 < 1h 变警示色；归零后本地即时转为「已过期」视图（不等 60s 周期），下载按钮隐藏。
4. **清除记录**（右上角按钮）：
   - 未过期 → `window.confirm` 二次确认 → 确认后调用 `DELETE /api/tasks/{id}` 删除视频文件 + 任务记录，失败（404 已不存在除外）alert 提示；
   - 已过期 → 直接清除，无确认。
5. **清除所有过期记录**（「下载任务」标签右侧按钮，仅存在过期记录时显示）：调用 `POST /api/tasks/purge-expired`，本地即时过滤过期卡。
6. **removed 事件**：`handleTaskUpdate` 收到 `status === "removed"` 即移除卡片——多标签页同步清除。

## 验证

- 新增 `tests/test_clear_records.py` 8 个用例：未过期清除（文件删+任务移+直链 404）/ 过期清除 / 运行中 409 / 不存在 404 / 批量仅清过期（免费过期+会员未过期对照组）/ 超 TTL completed 立即清除 / 孤儿文件清理（引用文件与新文件保留）/ 序列化字段断言（expires_at ≈ completed_at+24h，运行中为 None）
- 全量测试 75 passed（基线 67 + 新增 8）；ruff check + format 通过；前端 `npm run build` 通过
- 倒计时边界：本地归零卡走「已过期」路径直接清除；后端 `purge_expired` 对超 TTL 的 completed 同样移除，两端一致

## 替换方案（未采纳）

| 方案 | 说明 | 评价 |
|------|------|------|
| 前端仅按 `status==expired` 判断 | 后端周期清理最长 60s 延迟，归零到标记期间显示 `00:00:00` 残留 | 体验差，采用本地视图判定（isExpiredView） |
| 后端定时批量删除 expired 任务 | cleaner 周期里顺带移除 expired | 与手动清除语义重叠，且后台无交互对象（前端靠 removed 事件同步），保留 purge 手动触发更可控 |
| 单条清除复用 purge-expired | 前端收集过期 id 逐个调 | 批量端点语义清晰，单条走 DELETE 幂等更好 |
