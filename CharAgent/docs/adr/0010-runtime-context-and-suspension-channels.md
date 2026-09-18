# ADR-0010: 运行时上下文注入与挂起信号两条通用通道

- 状态: accepted
- 日期: 2026-09-18
- 修订: ADR-0008 的「框架层不变」表述（该表述针对**业务对接形态**成立；本 ADR 是框架侧新增的**通用能力**，不因业务而特化）
- 考虑过的方案:
  - **上下文注入用 ContextVar**（应用在 `loop.run()` 前 set，工具函数体 get）—— 拒绝：隐式依赖，看工具签名不知道它需要身份，漏 set 到运行时才 401；且与「@tool 签名即契约」的显式风格不一致
  - **上下文注入用闭包工厂**（`make_tools(ctx)` 按会话重建工具集）—— 拒绝：工具集与会话绑死，`AgentLoop` 无法跨 run 复用，与 [loop.py](../../agent/loop.py) 类 docstring 写下的设计意图（「构造参数为一次 run 的静态配置……便于 server 层按 run 复用同一 AgentLoop」）相反
  - **挂起信号用专用异常** `ToolSuspension` —— 拒绝：破坏 `execute_tool` 的「**永不抛异常**」契约（P0 docstring 明写），且异常路径在类型上不可枚举
  - **挂起用静态标记** `@tool(requires_approval=True)` + 执行前判定钩子 —— 拒绝：**本场景原理上不成立**。金额分层要求「可退金额 ≤ 阈值才自动通过」，而金额只有工具调完业务系统才知道；执行前钩子拿不到，除非把一次业务动作拆成预检 + 执行两个工具（碎）
  - **控制面只留 Python API，端点由应用自建** —— 拒绝：每个应用都要重写一遍「列挂起 + 恢复」，且前端轮询要自己定义形状
- 后果: 动 P0 代码（`tool/decorator.py` schema 生成、`tool/executor.py`、`agent/loop.py`、`db/state.py` 状态机 + `RunsRepository` 新查询）；ADR-0008 的「框架层不变」需按修订说明理解

## 上下文

P0 的六个演示工具全是**微秒级纯函数**：无外部依赖、无身份、无副作用。因此当时的框架不需要两条通道——「工具从哪拿身份」和「工具怎么让 loop 停下来」都不存在。

P1 的业务场景把这两件事同时变成了刚需（ADR-0008 的委托身份 + ADR-0009 的写操作分级），而 P0 的调用链上**两个方向都不通**：

| 方向 | 需要的通道 | P0 现状 |
|------|-----------|---------|
| loop → 工具 | 委托身份 / 凭据（工具签名里故意没有 `user_id`） | `execute_tool(tool, *, arguments)` 无 context 形参；`Tool` 无 context 槽位；hook 五个触发点都在错误时机（`on_tool_executed` 在执行**之后**才 fire），无一能改写实参 |
| 工具 → loop | 「我是高危操作，请挂起」 | 工具只有两条出口：返回值（被 `_stringify` 文本化回填）或抛 `ToolActionableError`（变成给模型的错误文本）——**两条都不改变控制流** |

结果是：框架**能从挂起恢复**（`resume` 补做欠下的工具调用），但**不会产生挂起**——`loop.py` 对 `Suspension` 的引用数为 0，`ToolCallStatus.NEEDS_APPROVAL` 在生产代码里**零写入者**。

复核（2026-09-18）确认这两件事在规划里**无主**：框架交接文档指向 `CharService/issues/01`、后者只写了结果不说手段、P1-1 只管会话级身份绑定。

## 决策

### 一、上下文注入：签名里声明、schema 里消失、执行时填充

工具用一个带标记的参数声明它需要上下文：

```python
@tool
def get_order_detail(
    order_no: Annotated[str, Field(pattern=r"^\d{14}$")],
    ctx: Annotated[ToolContext, Injected()],
) -> str | Suspension: ...
```

- **schema 生成器跳过 `Injected` 参数** → 模型看到的 schema 里只有 `order_no`，`user_id` 依然无处可填（ADR-0009 的防注入性质不变）
- **`execute_tool(tool, *, arguments, context)`** 在执行时填充
- **`AgentLoop.run(messages, run_id, context=...)`** 携带 run 级上下文 → **loop 仍可跨 run 复用**，P0 的设计意图保住
- 形态与业界同款：LangChain `InjectedToolArg` / Pydantic AI `RunContext` / FastAPI `Depends`

`ToolContext` 是**框架定义的通用容器**（不是业务对象）：由应用构造并注入，框架只负责传递。里面装什么（委托 token、principal、网关客户端）由应用决定。

### 二、挂起信号：工具返回 `Suspension`

工具自己判定（因为只有它拿得到业务数据），返回一个 `Suspension` 对象：

```python
view = await ctx.gateway.get(f"/orders/{order_no}/support-view")
if view.refundable <= LIMIT:            # ← 阈值是业务配置，不在框架里
    return await ctx.gateway.post("/refunds", ...)
return Suspension(                       # ← 停下
    kind=SuspensionKind.APPROVAL,
    reason="refund_over_limit",
    payload={"amount": view.refundable, "order_no": order_no},
)
```

- executor 识别 `Suspension` 并放进 **`ToolExecution.suspension`**（不走 `_stringify` 文本化）
- loop 检测到非空即：落帧 → 置等待态 → 发对应事件 → 停下
- **两种等待态用同一个 `Suspension` 的不同 `kind` 表达**（内部审批 / 用户确认），与 ADR-0009「两条路径语义不同、代码不得耦合」对得上
- **阈值、规则、「哪些算高危」全在应用侧**——框架只提供通道。这条把 ADR-0008「业务规则归业务系统」贯彻到了挂起机制上

### 三、控制面：两个通用运行时端点

```
GET  /runs?status=waiting_approval|waiting_confirmation
     → [{run_id, thread_id, suspension: {kind, reason, payload}}]
POST /runs/{run_id}/resume
     → load_latest(thread_id) → loop.resume(checkpoint)
```

- 「**哪些 run 在等人**」是任何 agent 应用都要问的问题，与业务无关
- `resume` 与已有的 `cancel` 是一对孪生操作（一个终止、一个继续），放同一层最自然
- 恢复**不需要原 loop 实例**——P0 的 `resume` 从 checkpoint 起步，可用新构造的 loop
- 配套补 `RunsRepository.list_by_status`（框架侧真缺的那块查询）

### 四、状态机补一态

原 8 态（`created/running/waiting_tool/waiting_user/retrying/failed/finished/cancelled`）只有 `waiting_user`。两种挂起语义要分开，故**新增「等待内部审批」态**——不复用 `waiting_user`（那是「等终端用户」，两者放行者与超时策略都不同，合并会与 ADR-0009 的「代码不得耦合」冲突）。

## 决策记录

- 注入用「签名声明 + schema 跳过 + 执行填充」，不用 ContextVar / 闭包工厂（2026-09-18 复核访谈 Q1）
- 挂起用「工具返回 `Suspension`」，不用专用异常 / 静态标记（2026-09-18 复核访谈 Q2）
- 控制面用框架的两个通用运行时端点，不用「应用自建」（2026-09-18 复核访谈 Q3）
- 状态机新增等待内部审批态（2026-09-18 复核访谈 Q2 连带）
