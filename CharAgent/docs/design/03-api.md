# 03 Server API 与事件协议

> 传输：REST（JSON）+ SSE（流式单向推送，ADR-0005）。前端：Vue 3 + EventSource。

## 1. REST 端点

> **2026-09-18 拆分**（ADR-0008 决策 9 分层剥离）：框架只交付**通用运行时端点**（会话 / run / SSE / 取消 / 挂起控制面 / healthz），以 router 工厂形式由应用组装；**业务端点**（审批台 / 接管台 / 工单 / 转人工）移入 `CharService/`，见 `.scratch/CharService/PRD.md`。

| 方法 | 路径 | 说明 | 关联难点 |
|------|------|------|---------|
| POST | `/api/v1/threads` | 创建会话（body: `{tenant_id, user_id, title?}`）→ `{thread_id}` | #12 |
| GET | `/api/v1/threads/{thread_id}/messages` | 拉取会话历史（断线重连用；**只含一问一答**，工具轨迹走管理端端点） | #16 |
| POST | `/api/v1/threads/{thread_id}/runs` | 发起一次执行（body: `{content, request_id?}`）→ `{run_id}`。长任务 HTTP 立即返回，进度走 SSE（#20） | #20 |
| GET | `/api/v1/threads/{thread_id}/runs/{run_id}/events` | **SSE 事件流**（§2），从 `after_event_id` 断点续拉 | #4 |
| POST | `/api/v1/runs/{run_id}/cancel` | 取消执行（kill switch，#18） | #18 |
| GET | `/api/v1/runs?status=<等待态>` | **列挂起 run** → `[{run_id, thread_id, suspension:{kind,reason,payload}}]`（ADR-0010） | #25 |
| POST | `/api/v1/runs/{run_id}/resume` | **恢复挂起 run**：从挂起 checkpoint 恢复执行（不重跑）。批准与拒绝都走它，语义差异由应用决定 | #25 |
| GET | `/api/v1/runs/{run_id}/transcript` | **管理端轨迹**：含 thinking / tool_call / tool_result 的完整历史（接管台用） | #19 |
| GET | `/healthz` | 健康检查（依赖探活：PG/Redis/Milvus/MySQL） | #64 |

**移到 `CharService/` 的端点**（不再是框架契约）：`GET /approvals`、`POST /approvals/{id}/approve|reject`、`POST /threads/{id}/escalate`、`POST /threads/{id}/reply`、`GET /tickets` —— 它们是客服业务，不是运行时能力。

幂等：所有 POST 接受 `request_id`（幂等键，#13/#17）——重复请求返回已有结果。键的框架层校验
已落地（P0，`retry/idempotency.py`）：长度 1~255、字符集 `[A-Za-z0-9._:-]`、首字符为字母或
数字 —— 非法键由 server 直接 400（该值会变成下游存储 key 与日志字段，通配 / 空白 / 控制字符
必须挡在入口）。重复提交的判定由 `IdempotencyStore` 承担：`CLAIMED` 首次执行、`IN_PROGRESS`
有在途、`COMPLETED` 直接返回既有结果。

## 2. SSE 事件协议

> 框架层实现：`CharAgent/stream/`（事件类型与状态机）+ `CharAgent/hooks/`（扩展点）；
> 事件由 `AgentLoop` 经 `event_sink` 产出（issue 05 交付，测试见 `tests/test_loop_events.py`）。

SSE 事件名（`event:` 字段）即 StreamEvent 类型，`data:` 为 JSON（框架层不含 `run_id`，
由 server 转发时注入；`seq` 即 `after_event_id` 的取值，也可映射为 SSE 的 `id:` 字段）：

```text
event: thinking                    # 非终止轮（工具轮）的助手正文（过程叙述）
data: {"type":"thinking","seq":1,"run_id":"...","turn":1,"message":"让我先查一下订单"}

event: tool_call                   # arguments 为原始 JSON 字符串（框架不预解析 #10）
data: {"type":"tool_call","seq":2,"run_id":"...","turn":1,"tool_call_id":"call_1",
      "tool_name":"query_order","arguments":"{\"order_no\":\"20260701123456\"}",
      "status":"started"}

event: tool_result                 # 成功带 summary（超长截断 + 省略号），失败带可操作 error
data: {"type":"tool_result","seq":3,"run_id":"...","turn":1,"tool_call_id":"call_1",
      "tool_name":"query_order","status":"ok","summary":"订单 20260701xxx 已发货",
      "duration_ms":120}

event: tool_call                   # 第二轮（并行语义：同一轮多条 tool_call）
data: {"type":"tool_call","seq":4,"run_id":"...","turn":2,"tool_call_id":"call_2",
      "tool_name":"query_order","arguments":"{\"order_no\":\"12345\"}","status":"started"}

event: tool_result
data: {"type":"tool_result","seq":5,"run_id":"...","turn":2,"tool_call_id":"call_2",
      "tool_name":"query_order","status":"error",
      "error":"order_no 应为 14 位数字, 实际 5 位: '12345'","duration_ms":3}

event: approval_required          # HITL 挂起（#25，P1-7 追加；不在 P0 状态机内）
data: {"type":"approval_required","run_id":"...","approval_id":"appr_1",
      "operation":"refund","amount":"128.00","context":"...","tool_call_id":"call_3"}

event: reasoning                   # 思维链（#11）；前端折叠展示，不混入正文
data: {"type":"reasoning","seq":6,"run_id":"...","turn":3,"delta":"正在核对订单..."}

event: final                       # 正常结束的终局事件（content 为权威值）
data: {"type":"final","seq":7,"run_id":"...","content":"您的退款申请已受理，将在 1-3 个工作日原路退回。",
      "finish_reason":"stop","outcome":"finished","tokens":1234,"elapsed_ms":3210.5}

event: error                       # 异常结束的终局事件
data: {"type":"error","seq":7,"run_id":"...","error":{"code":"max_turns","message":"已达最大轮数限制, 未能产出最终答复"}}
```

### 2.1 事件状态机（框架层强校验，违反即报错）

`EventBus` 在事件产出的瞬间校验四条不变量（违反抛 `EventSequenceError`，不让乱序流
进入前端）：

1. `seq` 每 run 从 1 起单调递增
2. `tool_result` 必须匹配一条未闭合的 `tool_call`（按 `tool_call_id` 配对）；同一 id 在
   **闭合前**不得重复开启 —— 只约束未闭合期间：真实上游的 `tool_call_id` 逐响应重置
   （如 `call_0` 每轮重来），跨轮复用同一 id 属正常现象
3. 仍有未闭合 `tool_call` 时不得发终局事件（工具结果必须回填完）
4. 终局事件之后不得再发任何事件（含第二个终局）

主序列：`[thinking] → (tool_call⁺ → tool_result⁺) → [thinking] → ... → final | error`。
`reasoning` 是**旁路通道**（任意非终局位置可发，不改变主序列），与 wire 历史的配对约束
（#10：`tool` 消息紧随带 `tool_calls` 的 `assistant`）是同一条规则在两条通道上的体现。

### 2.2 终局事件的选择规则（定案）

**判据是「run 是怎么结束的」，不是「有没有正文」。**

| 结束情形 | 终局事件 | `error.code` |
|---------|---------|-------------|
| 模型正常作答（`stop`；含极少数「正常结束但没吐正文」，此时 `content` 为 `null`） | `final` | — |
| `max_turns` / `token_budget` / `time_limit` / `truncation_limit`（guard 刹车，没有可用答复） | `error` | 取 `LoopOutcome` 值 |
| 上游中断（资源不足 / 被中断，半截不可用） | `error` | `server_interrupted` |
| 输出被安全策略拦截（`content_filter`） | `error` | `content_filter` |

- 定案理由：刹车时 `LoopResult.content` 为 `None`，发 `final` 等于「终局答复却没有答复」，
  前端语义坏掉；**降级话术（模板回复 / 转人工）是产品文案，归 P1-8 server 层**，框架只给
  事实性说明
- `content_filter` 按 **`finish_reason` 归类**（loop 语义上 `outcome` 仍是 `FINISHED`）：
  即便模型已吐出部分文本，被安全策略拦下的内容也不该当可展示答复推给前端（拦截语义归
  P1-6 输出护栏）
- 框架层 `error.code` 说明「run 为什么停」；§4 的 API 级错误码说明「服务打算怎么降级」，
  由 server 在熔断 / 超时 / 限流 / 降级处产出（P1），两者不冲突
- 取消（kill switch）：`CancelledError` 在 loop 内直接传播（不吞），`error(cancelled)`
  收尾由 P1-2 server 层产出
- 框架层异常（模型调用失败等）直接抛出、**不发终局事件** —— 事件流中断即如实反映失败，
  由 server 按 §4 发 error 给前端

### 2.3 delta 的从属地位与 final 的权威性（#10）

- `delta` / 过程事件只作**渐进预览**，`final.content` 是**权威值**：前端收到 `final`
  即用它覆盖渲染缓冲
- 这条规则是为截断场景立的：CONTINUE 续写的各段、CONDENSE 丢弃的截断前缀，都可能在缓冲里
  留下已作废内容，只累加会显示错内容
- P0 现状：`ChatModel.generate` 返回完整响应，尚无 token 级 content delta 通道，故
  `reasoning` 每次响应产出**一条完整 delta**（前端按增量累加语义处理即可）；token 级切分
  待 P1 给模型协议加 delta 回调后接入，届时本规则自动生效
- `final.content` 与 `LoopResult.content` 恒等（含 CONTINUE 跨轮拼合后的结果）
- 截断轮的正文**不发 `thinking`**：它是答案素材而非过程叙述 —— CONTINUE 段会拼进 `final`
  （发了会重复展示），CONDENSE 段已被丢弃（发了等于把作废内容推给用户）

### 2.4 其他要点

- `tool_result` 的 error 必须**可操作**（#2：说清字段格式期望，不甩 422）；`summary` 只带
  截断摘要（超长时保留 200 字符并追加省略号），工具返回全文在 wire 历史与 `GET messages` 里
- `reasoning` 增量供前端**折叠展示**（Thinking 区），与正文 content 分属两条通道（#11）。
  注意与 wire 历史区分：官方文档要求带 `tools` 的请求回传 `reasoning_content` 并拼接进
  模型侧上下文（本机实测 2026-09-11 未强制），这与「是否展示给用户」无关 —— 两码事
- `thinking` 承载**工具轮**的助手正文（过程叙述，会被 loop 从最终答案里剔除）；终止轮正文
  才是 `final`。无叙述的工具轮不发 `thinking`（不产空事件）
- `tool_call.arguments` 原样透出 JSON 字符串（框架不预解析，#10 保真；畸形 JSON 正是自纠错
  路径的信号）
- `approval_required` 挂起后事件流保持连接，审批通过续推后续事件（P1-7 追加该事件类型）
- 后续追加字段：`final.citations`（P1-9 RAG 引用溯源）与 `final.cost`（P2 cost 插件）——
  P0 的 `final` 只含 `content` / `finish_reason` / `outcome` / `tokens` / `elapsed_ms`

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
  工具返回 Suspension → |<-- approval_required ----|   run 挂起，checkpoint 落盘（挂起点持久化）
  (管理端) -- GET /runs?status=... -->|   列挂起（应用侧审批台轮询/刷新）
  (管理端) -- POST /runs/{id}/resume ->|
                       |<-- tool_result ----------|   从挂起 checkpoint 恢复，继续执行
```

- **挂号与放行是两个端点**：应用先查 `GET /runs?status=` 拿到 `suspension` 载荷（含 kind / reason / payload），批准时调 `POST /runs/{id}/resume`。**拒绝也调 resume**——「拒绝时怎么把原因回填」由应用决定，框架不区分。
- 审批超时（默认值可配；框架不写死业务时限）→ 自动拒绝，失败原因回填模型走降级（#25 超时处理）。
- **业务端点不在此**：审批台的业务视图、角色校验、SoD 规则都在 `CharService/`。

### 3.3 转人工（#19）—— **业务端点，已移入 `CharService/`**

```text
  用户: "我要投诉，转人工"
  agent: 确认意图 → 无自动方案 → 建议转人工
  (用户确认) -- POST escalate -->    ← CharService 的业务端点，不在框架契约里
  server: 创建 ticket + escalation，run 状态 closed，SSE 发 final(转人工提示)
  人工: -- GET /runs/{id}/transcript -->   查看完整历史（含工具轨迹，框架端点）
  人工: -- POST reply ---------------->   写 assistant 消息（CharService 业务端点）
```

### 3.4 取消（#18）

```text
  -- POST cancel -->  runtime: asyncio.Task.cancel → 工具协程取消、释放连接
                     → 已执行真实副作用：幂等键记录，不走补偿（未完成动作无副作用）
                     → run 状态 cancelled，SSE 发 error(cancelled) 收尾
```

## 4. 错误码与降级（#19）

> **2026-09-18 归位**：**码与语义边界归框架，降级出口归应用**。右列的「模板回复 / FAQ 匹配 / 转人工建议」是产品策略（[issue 05 §3](../../../.scratch/CharAgent/issues/05-P0-4-stream-events-hooks.md) 早已定过），随 demo 外移归 `CharService/`；框架侧交付的是分类、映射与可注册的降级策略 SPI（[issue 18](../../../.scratch/CharAgent/issues/18-P1-8-degradation.md)）。

| code | 含义（语义边界） | 降级出口（应用侧） |
|------|-----------------|------------------|
| `LLM_DOWN` | 模型调用失败：**重试耗尽后仍失败 / 熔断打开** | 模板回复 + 转人工建议 |
| `LLM_TIMEOUT` | 模型超时（单请求超过配置时限） | 返回已算部分结果或模板回复 |
| `RAG_DOWN` | 检索依赖故障（向量库 / embedding） | FAQ 规则匹配 |
| `TOOL_ERROR` | 工具全部失败且自纠错无效 | workflow 兜底路径 / 转人工 |
| `RATE_LIMITED` | 限流（#22） | 排队或 429 + 重试提示 |
| `DEPENDENCY_DOWN` | **下游依赖不可用**（通用码；2026-09-18 由 `GATEWAY_DOWN` 改名——框架不该知道「网关」这个业务拓扑） | 只读仍可用、写操作明确拒绝并建议转人工 |
| `CANCELLED` | 用户取消 | 无（正常收尾） |
| `BUDGET_EXCEEDED` | 预算硬上限（#34，P2） | 拒绝 + 提示 |

> 本表是 **API 级**错误码（面向用户的服务降级决策）。框架层 `error` 事件（§2.2）用的是
> 「结束原因」码（`LoopOutcome` 值 + `content_filter`），说明 run 为什么停；框架提供
> **`LoopOutcome → 本表 code` 的映射**，应用据此选降级路径（映射本身不含话术）。

> 框架层重试（`retry/` 的 `RetryingChatModel`）发生在**模型调用层**：本表的 `LLM_DOWN` /
> `LLM_TIMEOUT` 是**重试耗尽后**的结果，不是第一次失败就上报。被重试丢弃的已计费尝试
> （异常路径拿不到 usage；上游中断那条路径拿得到）由 `on_retry` 的 `RetryAttempt` 摊开，
> 供 P1-11 token 计量 —— P0 不把它并入 `LoopGuard` 的 token 预算（避免给假账），预算裁决属 P1-11。

## 5. 前端页面（单 Vue 项目双路由）

| 路由 | 页面 | 承载 |
|------|------|------|
| `/chat` | 用户聊天窗 | SSE 事件可视化（thinking/tool_call/tool_result/final 渐进渲染）、reasoning 折叠展示、取消按钮、转人工按钮 |
| `/admin` | 审批台 + 接管台 | 挂起审批列表（上下文可见、批准/拒绝）、转人工会话接管（历史 + 回复） |
