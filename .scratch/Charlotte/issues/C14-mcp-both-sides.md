# C14 · MCP：消费侧接缝 + 暴露侧 server

**Status:** todo

**Type:** feature

**Blocked by:** C09（暴露侧要导出的是含检索工具的完整工具集）

**上游:** `CharAgent/docs/DESIGN.md` §4 ⑨ 的 #50；`CharApp/docs/adr/0001`（内部端点信任调用方声明的 `X-User-Id`）；`.scratch/Charlotte/PLAN.md` §6.1 的 D1

## 现状（2026-09-30 读码）

| 事实 | 证据 |
|---|---|
| **SDK 已装、零 import** | `requirements.txt:169 mcp==1.29.0`、`:142 langchain-mcp-adapters==0.3.2` —— 但 `CharAgent` / `CharApp` / `app` / `demo` **全库零 import**（唯一命中是 `rag_knowledge` 里一段**被注释掉**的代码） |
| 接缝现成 | `ToolProvider` 是**单方法异步协议**（`agent/provider.py:102-119`）：`async def provide(ctx) -> Sequence[Tool]`，不继承基类、不缓存 |
| 工具可程序化构造 | `Tool` 是 `@dataclass(slots=True)`（`tool/decorator.py:52-90`），`parameters` 就是 wire JSON schema —— **MCP 的 `inputSchema` 可以直接填进去，不必用 `@tool` 装饰器** |
| 规划已在 | `DESIGN.md` #50 点名了三类能力（tools / resources / prompts）、生命周期、sampling、**多 server 的工具发现与同名仲裁** |

**两侧都要做**（D1 的结论）：消费侧证明**框架抽象装得下生态标准**；暴露侧证明**业务能力能被任何 MCP 客户端调用**，而且它能当场演示。

---

## 一、消费侧：`McpToolProvider(ToolProvider)`

```
initialize → tools/list → 每个 MCP tool 转成一个 Tool 对象 → provide() 返回
```

- **`inputSchema` 直接当 `Tool.parameters`** —— 两边都是 JSON Schema，中间不需要翻译层
- 调用时：`tools/call` 的结果转成字符串回填（`Tool` 的契约是返回 `str`）
- **同名仲裁**（DESIGN #50 点名）：多 server 场景下工具重名要有确定的规则（建议**前缀 + 拒绝静默覆盖**，重名时 fail fast 而不是后注册的赢）
- **生命周期**：连接何时建、何时关、断了怎么办 —— 与本项目的 `dirty` 纪律对齐：**不要静默降级成「工具少了一个」**
- 配置文件：消费哪个 server、用什么命令起（`command` + `args` 的既有 MCP 约定）

**消费侧的目标不是「找个业务相关的 server」**，而是**一条契约测试**：能把一个**真实**的参考 server 的工具列出来 → 转成 `Tool` → 交给 `AgentLoop` 跑通一次。为了"业务相关"硬凑一个场景，反而会稀释「抽象通用」这个论点。

## 二、暴露侧：把 `CharApp` 的工具包成 MCP server

- **进程形态**：独立进程、**stdio 传输**（这样 Claude Code / Claude Desktop 配一行就能连）
- **工具定义机械映射**：`CharApp` 的工具已经是 `name` / `description`（docstring 首段）/ `Annotated + Field` 生成的 schema —— 逐字段搬即可
- **只暴露只读那几个**（`get_my_order` / `list_my_orders` / `get_my_profile` / `search_products` / `search_knowledge`…）。**写工具（下单 / 付款 / 退款）不暴露** —— 理由：MCP 客户端没有你的确认卡机制，暴露写操作等于**绕过 HITL 直接下单**。这条要写进 ADR

### ⚠️ 信任边界：这一票最需要设计的地方

你的 `X-Internal-Token` + `X-User-Id` 模型（ADR-0001）成立的前提是「**同一个信任域内转发**」—— 浏览器 **session cookie 是唯一的真实认证点**，`X-User-Id` 只是同一域内的转发。

**MCP 客户端不在这个信任域里。** 它没有 session cookie。裸采信外部客户端声明的 `user_id`，就是把 ADR-0001 的前提拆了。

**处置（D1 的结论）**：本版按「**本地演示用**」做 ——

1. server 启动时从**环境变量**读一个固定的 `user_id`，绑定到单个账户
2. 工具 schema 里**不出现任何身份参数**（模型改不了）
3. 文档与 ADR 里写明边界：**这个 server 代表配置里那一个账户**，不是通用多用户网关
4. **生产化路径写进 ADR**：内部 JWT（`sub` claim）或 OAuth —— 但要说明**换的只是传输，验证点不变**

> **这条的叙述价值很高**：ADR-0001 自己写了「**什么时候必须还**」的判据 ——「信任域被拆开」。而 MCP 暴露侧**正好把信任域拆开了**。**你按自己半年前定的判据做了处置** —— 这比"我知道 MCP 有安全问题"强得多。

---

## 三、ADR

至少两条：**① MCP 暴露侧只开只读 + 固定账户的边界与生产化路径**；**② 消费侧的同名仲裁规则**（若规则不显然）。

---

## 验收

- [ ] 消费侧：一条契约测试 —— 起一个真实 MCP server，`McpToolProvider` 列出它的工具、转成 `Tool`、经由 `AgentLoop` 跑通一次调用
- [ ] 消费侧：同名工具仲裁有测试（两个 server 同名时**不静默覆盖**）
- [ ] 暴露侧：在 Claude Code（或 Claude Desktop）里配上，问「我的订单到哪了」→ **工具真打到你的 Django 商城**，返回真数据
- [ ] **否定断言**：暴露侧的工具列表里**没有**任何写工具（下单 / 付款 / 退款）
- [ ] **否定断言**：暴露侧工具的 schema 里**没有** `user_id` 之类的身份参数
- [ ] `AgentLoop` 零改动（同 L1a 的纪律：加一层能力不需要动循环核心）
- [ ] 两条 ADR 已记
- [ ] `CharAgent` / `CharApp` 既有用例全绿

## 开工前要定的

- **消费侧接哪个 server** —— 建议挑一个**官方参考实现**（`filesystem` / `fetch` / `sqlite` 之类），标准是「你的 `ToolProvider` 抽象能原样装下」；**不要为了业务相关硬凑**
- 暴露侧用哪个 SDK 写法（`mcp` 官方 Python SDK 的 server 端）
- 消费侧是做成 `CharAgent` 的一个模块还是独立包（建议**模块** —— 它属框架能力，不是业务）

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
