# ADR-0007: LLM 输出流式化 — 总结与 AI 问答打字机

- 日期：2026-08-21
- 状态：已接受

## 背景

ADR-0005 的三个 LLM 调用点（`summarize` / `generate_mindmap` / `ask`）均为
非流式：openai 兼容 SDK 直接 `chat.completions.create()` 等待完整响应。用户
体验问题：

- **总结 tab**：子任务 running 期间只有不确定进度条，LLM 生成过程不可见，
  done 后才一次性拉取渲染（生成一个 3h 视频总结可能等数分钟）
- **AI 问答 tab**：提问后显示「思考中…」spinner，完整回答一次性 push 消息

需求：两个 tab 的回答均改为**流式输出**（打字机效果），生成过程实时可见。

## 决策

1. **LLM 流式实现：openai SDK 直接 `stream=True`**，不引入 agents SDK。
   `_chat_stream(messages)` 迭代 `chunk.choices[0].delta.content`
   增量 yield，跳过空块（usage 尾块 `not chunk.choices`、`delta.content` 空），
   `stream.close()` 放 finally（客户端中途放弃时关闭底层 HTTP 流），错误包装
   为 `LLMError`。`summarize_stream` 与 `ask_stream` 均为其薄封装；
   `summarize_stream` yield Markdown 总结文档增量（ADR-0008），最终 dict 由
   调用方流结束后调 `parse_summary_text` 解析（Markdown 解析器，ADR-0008；
   缺章节时间线 → LLMError → 子任务 failed，错误路径与 ADR-0005 一致）。
   **逐块不 strip**：strip 会吞 chunk 边界空格丢字。
2. **总结流经「锁内缓冲 + 轮询快照」，不用事件总线**。高频文本增量会打爆
   `EventBus._QUEUE_MAXSIZE=100` 丢帧。`Task.summary_stream: list[str]` 为 chunk
   列表（不用 `str +=`：GIL 不保护读-改-写，会丢增量），worker 锁内 append，
   SSE 端点锁内快照（`summary_stream_snapshot` 一次取齐 状态/error/文本副本）。
   重试/重跑前锁内 `clear()`，防新旧文本拼接。
3. **`GET /summary/stream` SSE 帧协议**：命名事件 + 单行 JSON data
   （`json.dumps` 编码换行，`ensure_ascii=False`）——
   `snapshot{text}`（首帧必发累积全文，断线重连恢复现场）/ `delta{text}`（仅
   新增量）/ `done{}` / `error{message}` / `heartbeat{}`（空闲 15s，防代理断连）。
   服务端每连接维护 chunk 游标，0.2s 轮询；子任务 pending/running 均可订阅
   （等转录完成期间挂起，心跳保活）；子任务 failed/blocked 发 error 收尾。
4. **`POST /qa` 改 SSE 流式**：`StreamingResponse` + sync 生成器（Starlette
   线程池迭代，每 yield 立即 send）。**配额语义**：`quota.check` 流开始前
   （超限 429 以 HTTP 状态返回，与旧契约一致）；`quota.use` 在生成器 finally，
   但仅完整输出 done 帧后才计数（success 标志）——失败/断开不计数，保持
   ADR-0005「失败不计数」语义。客户端断开时 sync 生成器线程无法被杀，会消费
   完 LLM 流到自然结束，finally 延迟但最终执行。
5. **前端订阅生命周期**：watch summary 子任务状态（running → 开流，离开
   running → abort 关闭）；断线且仍 running → 指数退避重订阅（1s/2s/4s 封顶
   10s，snapshot 首帧兜底不丢文本）；done 帧到达时若总结未拉取直接
   `loadSummary()`（SSE 端点与事件总线竞态兜底）；组件卸载 abort + 清定时器。
   问答流：push 空文本占位 assistant 消息（替代 spinner），delta 帧追加，
   error 帧抛错 → 移除气泡 + 提示；卸载 abort。

## 后果

- 生成过程实时可见：总结 tab 展示 LLM Markdown 文档打字机（流式期间 marked
  实时渲染，完成后切换既有 Markdown 渲染，ADR-0008），问答 tab 流式打字机
- `POST /qa` 契约从 JSON 响应改为 SSE（前端消费方同步迁移，无其它调用方）
- 流式端点每 0.2s 锁内快照一次，文本量大时 CPU 占用略增（可接受：单任务
  低频轮询，列表级推送仍走事件总线）
- 断线重连语义清晰：summary 流重连不丢文本（snapshot），qa 流失败需重新提问
- 测试面新增流式断言（帧顺序 / 快照恢复 / 配额仅成功计数 / 重试清缓冲）
