# 02 — 下载 + 交付直链链路

**What to build:** 用户选定清晰度档位创建下载任务，任务在队列中执行，完成后生成临时交付直链，通过直链可下载保存为 MP4 文件；用户可查看全部任务及其状态。

**Blocked by:** 01 — 后端骨架 + 解析链路

**Status:** resolved

**验收标准：**
- [x] `POST /api/downloads` 有效请求（url + format_id）→ 200 返回 task_id；无效档位 → 明确错误
- [x] 任务状态机完整流转：pending → resolving → resolved → queued → downloading → completed（成功）/ failed（失败且携带错误信息）
- [x] 并发调度生效：同一时刻仅一个下载任务在执行（免费档 1 并发槽）
- [x] 输出为单一 MP4 文件（音视频流由 ffmpeg 合并）
- [x] `GET /api/tasks` 按创建时间降序返回任务列表（含状态 / 进度 / 消息）
- [x] `GET /api/files/{id}` 对 completed 任务返回文件流（Content-Disposition: attachment）
- [x] pytest 全部通过（TestClient 走通 创建 → 完成 → 直链下载 全链路 + 失败路径）
- [x] 真实链接下载 E2E 通过（脚本级，产出真实 MP4 文件）

## Comments

- 2026-08-19: T02 完成。实现: POST /api/downloads (解析 → 校验档位 → 入队) + 后台调度器 (免费 1 并发槽, FIFO, RLock 原子派发) + GET /api/tasks 降序 (progress/message) + GET /api/files/{id} 直链文件流 + 引擎下载封装 (merge_output_format=mp4, 进度 hook 上限 99, 错误透传)。测试: 17 个 pytest 全绿 (TestClient + mock `backend.downloader._download`, 含并发单槽 / 失败路径 / 直链下载)。真实 E2E: `scripts/e2e_download.py` 走通 解析 → 下载 → 直链取回, B 站 MV 产出 9.2MB 有效 MP4 (ftyp 校验 + ffprobe 时长 212s)。/code-review 双轴审查通过, 修复: 调度周期 0.5s / 引擎外异常 → failed / 进度 unknown-total 兜底 / TaskOut 转换公共化。提交: `593fe5f`。
