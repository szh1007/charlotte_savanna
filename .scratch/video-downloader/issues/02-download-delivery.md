# 02 — 下载 + 交付直链链路

**What to build:** 用户选定清晰度档位创建下载任务，任务在队列中执行，完成后生成临时交付直链，通过直链可下载保存为 MP4 文件；用户可查看全部任务及其状态。

**Blocked by:** 01 — 后端骨架 + 解析链路

**Status:** ready-for-agent

**验收标准：**
- [ ] `POST /api/downloads` 有效请求（url + format_id）→ 200 返回 task_id；无效档位 → 明确错误
- [ ] 任务状态机完整流转：pending → resolving → resolved → queued → downloading → completed（成功）/ failed（失败且携带错误信息）
- [ ] 并发调度生效：同一时刻仅一个下载任务在执行（免费档 1 并发槽）
- [ ] 输出为单一 MP4 文件（音视频流由 ffmpeg 合并）
- [ ] `GET /api/tasks` 按创建时间降序返回任务列表（含状态 / 进度 / 消息）
- [ ] `GET /api/files/{id}` 对 completed 任务返回文件流（Content-Disposition: attachment）
- [ ] pytest 全部通过（TestClient 走通 创建 → 完成 → 直链下载 全链路 + 失败路径）
- [ ] 真实链接下载 E2E 通过（脚本级，产出真实 MP4 文件）
