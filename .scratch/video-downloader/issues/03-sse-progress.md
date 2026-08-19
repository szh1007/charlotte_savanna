# 03 — SSE 进度流

**What to build:** 用户无需轮询即可实时看到任务进度变化：后端通过 SSE 向前端单向推送任务状态更新事件（解析中 / 下载中百分比 / 完成 / 失败 / 直链地址）。

**Blocked by:** 02 — 下载 + 交付直链链路

**Status:** ready-for-agent

**验收标准：**
- [ ] `GET /api/events` 可建立 SSE 连接（Content-Type: text/event-stream）
- [ ] 任务状态变化时推送 `event: task-update`，data 为 JSON，含 task_id / status / progress / message / url? / error?
- [ ] 空闲连接每 15 秒收到一次心跳事件
- [ ] 客户端断开后连接与订阅资源被正确清理（无泄漏）
- [ ] 事件广播线程安全（后台线程 → asyncio 循环，无竞态）
- [ ] pytest 全部通过（消费事件流断言事件序列、心跳、断开清理）
- [ ] curl 冒烟验证 SSE 流输出
