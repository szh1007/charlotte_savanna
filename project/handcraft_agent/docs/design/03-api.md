# 03 Server API 与事件协议

> 传输：REST（JSON）+ SSE（流式单向推送，ADR-0005）。前端：Vue 3 + EventSource。

## 1. REST 端点

| 方法 | 路径 | 说明 | 关联难点 |
|------|------|------|---------|
| POST | `/api/v1/threads` | 创建会话（body: `{tenant_id, user_id, title?}`）→ `{thread_id}` | #12 |
| GET | `/api/v1/threads/{thread_id}/messages` | 拉取会话历史（断线重连/人工接管用） | #16 |
| POST | `/api/v1/threads/{thread_id}/runs` | 发起一次执行（body: `{content, request_id?}`）→ `{run_id}`。长任务 HTTP 立即返回，进度走 SSE（#20） | #20 |
| GET | `/api/v1/threads/{thread_id}/runs/{run_id}/events` | **SSE 事件流**（§2），从 `after_event_id` 断点续拉 | #4 |
| POST | `/api/v1/runs/{run_id}/cancel` | 取消执行（kill switch，#18） | #18 |
| GET | `/api/v1/approvals?status=pending` | 审批台列表（管理端） | #25 |
| POST | `/api/v1/approvals/{approval_id}/approve` | 批准挂起操作 → 从挂起 checkpoint 恢复 run | #25 |
| POST | `/api/v1/approvals/{approval_id}/reject` | 拒绝 → 失败原因回填模型继续推理 | #25 |
| POST | `/api/v1/threads/{thread_id}/escalate` | 转人工（body: `{reason}`）→ 创建 ticket + escalation | #19 |
| POST | `/api/v1/threads/{thread_id}/reply` | 人工客服回复（接管后，写 assistant 消息） | #19 |
| GET | `/api/v1/tickets?status=...` | 工单列表/详情 | #19 |
| GET | `/healthz` | 健康检查（依赖探活：PG/Redis/Milvus/MySQL） | #64 |

幂等：所有 POST 接受 `request_id`（幂等键，#13/#17）——重复请求返回已有结果。

## 2. SSE 事件协议

SSE 事件名（`event:` 字段）即 StreamEvent 类型，`data:` 为 JSON：

```text
event: thinking
data: {"type":"thinking","run_id":"...","message":"正在理解您的售后问题..."}

event: tool_call
data: {"type":"tool_call","run_id":"...","tool_call_id":"call_1","tool_name":"query_order",
      "arguments":{...},"status":"started"}

event: tool_result
data: {"type":"tool_result","run_id":"...","tool_call_id":"call_1","tool_name":"query_order",
      "status":"ok","summary":"订单 20260701xxx 已发货","duration_ms":120}

event: tool_result
data: {"type":"tool_result","run_id":"...","tool_call_id":"call_2","status":"error",
      "error":"参数错误：order_no 格式应为 14 位数字，例如 20260701123456"}

event: approval_required          # HITL 挂起（#25）
data: {"type":"approval_required","run_id":"...","approval_id":"appr_1",
      "operation":"refund","amount":"128.00","context":"...","tool_call_id":"call_3"}

event: reasoning                   # 推理模型思维链增量（#11，不入历史）
data: {"type":"reasoning","run_id":"...","delta":"正在核对订单..."}

event: final
data: {"type":"final","run_id":"...","content":"您的退款申请已受理，将在 1-3 个工作日原路退回。",
      "citations":[{"doc_id":"...","source":"退换货政策.pdf"}],"tokens":{...},"cost":0.012}

event: error
data: {"type":"error","run_id":"...","error":{"code":"LLM_DOWN","message":"..."}}
```

要点：
- 每事件带 `seq`（事件序号），客户端记录 `after_event_id` 断点续拉
- `tool_result` 的 error 必须**可操作**（#2：说清字段格式期望，不甩 422）
- `reasoning` 增量推送展示但不存历史、不计入后续上下文（#11）
- `approval_required` 挂起后事件流保持连接，审批通过续推后续事件

## 3. 关键流程时序

### 3.1 正常问答（含工具调用）

```text
client                     server
  |-- POST runs ------------>|  创建 run，入 TaskQueue
  |<-- {run_id} -------------|
  |-- GET events (SSE) ----->|
  |<-- thinking -------------|
  |<-- tool_call ------------|
  |<-- tool_result ----------|
  |<-- tool_call ------------|  (多轮)
  |<-- tool_result ----------|
  |<-- final ----------------|
  |<-- [stream closed] ------|
```

### 3.2 HITL 审批（#25）

```text
  |<-- approval_required ----|   run 挂起，checkpoint 落盘（挂起点持久化）
  (管理端) -- GET approvals ->|   审批台轮询/刷新
  (管理端) -- POST approve --->|
  |<-- tool_result ----------|   从挂起 checkpoint 恢复，继续执行
```

审批超时（未定时间，默认 15 分钟）→ 自动拒绝，失败原因回填模型走降级（#25 超时处理）。

### 3.3 转人工（#19）

```text
  用户: "我要投诉，转人工"
  agent: 确认意图 → 无自动方案 → 建议转人工
  (用户确认) -- POST escalate -->
  server: 创建 ticket + escalation，run 状态 closed，SSE 发 final(转人工提示)
  人工: -- GET messages --> 查看完整历史
  人工: -- POST reply ------> 写 assistant 消息（可见于历史）
```

### 3.4 取消（#18）

```text
  -- POST cancel -->  runtime: asyncio.Task.cancel → 工具协程取消、释放连接
                     → 已执行真实副作用：幂等键记录，不走补偿（未完成动作无副作用）
                     → run 状态 cancelled，SSE 发 error(cancelled) 收尾
```

## 4. 错误码与降级（#19）

| code | 含义 | 降级路径 |
|------|------|---------|
| `LLM_DOWN` | 模型调用失败（熔断打开） | 模板回复 + 转人工建议 |
| `LLM_TIMEOUT` | 模型超时 | 返回已算部分结果或模板回复 |
| `RAG_DOWN` | Milvus/embedding 故障 | FAQ 规则匹配（Redis 精确/相似命中） |
| `TOOL_ERROR` | 工具全部失败且自纠错无效 | workflow 兜底路径 / 转人工 |
| `RATE_LIMITED` | 限流（#22） | 排队或 429 + 重试提示 |
| `CANCELLED` | 用户取消 | 无（正常收尾） |
| `BUDGET_EXCEEDED` | 预算硬上限（#34，P2） | 拒绝 + 提示 |

## 5. 前端页面（单 Vue 项目双路由）

| 路由 | 页面 | 承载 |
|------|------|------|
| `/chat` | 用户聊天窗 | SSE 事件可视化（thinking/tool_call/tool_result/final 渐进渲染）、reasoning 折叠展示、取消按钮、转人工按钮 |
| `/admin` | 审批台 + 接管台 | 挂起审批列表（上下文可见、批准/拒绝）、转人工会话接管（历史 + 回复） |
