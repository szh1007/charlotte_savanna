# 03 — SSE 进度流

**What to build:** 用户无需轮询即可实时看到任务进度变化：后端通过 SSE 向前端单向推送任务状态更新事件（解析中 / 下载中百分比 / 完成 / 失败 / 直链地址）。

**Blocked by:** 02 — 下载 + 交付直链链路

**Status:** resolved

**验收标准：**
- [x] `GET /api/events` 可建立 SSE 连接（Content-Type: text/event-stream）
- [x] 任务状态变化时推送 `event: task-update`，data 为 JSON，含 task_id / status / progress / message / url? / error?
- [x] 空闲连接每 15 秒收到一次心跳事件
- [x] 客户端断开后连接与订阅资源被正确清理（无泄漏）
- [x] 事件广播线程安全（后台线程 → asyncio 循环，无竞态）
- [x] pytest 全部通过（消费事件流断言事件序列、心跳、断开清理）
- [x] curl 冒烟验证 SSE 流输出

**Comments（实现摘要）：**

- 事件总线 `backend/events.py`：`EventBus` 订阅中心（锁保护），每个 SSE 连接独立 `asyncio.Queue(maxsize=100)`，后台线程 publish 经 `call_soon_threadsafe` 投递（含停机 `loop.is_closed()` 防御；队列满丢弃过旧快照）
- 广播点 `task_manager.update_status`：唯一状态变更入口（resolving/failed/resolved/queued/downloading/completed 全覆盖），锁内统一广播状态快照
- SSE 路由 `routers/events.py`：连接建立先推当前任务快照（断线重连恢复现场），`?task_ids` 过滤（非法参数 422），15s 心跳兼断连探测（send 失败 → 生成器 finally 清理订阅）
- 测试：httpx ASGITransport / Starlette TestClient 不支持流式响应，自定义 ASGI 流式客户端 `tests/sse_client.py`（独立线程驱动 app，帧级消费，close 模拟真实断开）；8 个验收测试覆盖连接/事件序列/心跳/断开清理（事件唤醒 + 心跳两路径）/过滤/422
- 审查加固：订阅队列有界、停机边界、类型注解（`TYPE_CHECKING` / `Callable`）、`contextlib.suppress` 替代 try-except-pass
- 验证：pytest 25 passed（T01/T02 17 + T03 8），curl 冒烟真实 B 站链接输出 resolving→resolved 事件流
