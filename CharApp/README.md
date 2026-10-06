# CharApp — 电商智能客服（CharAgent 的业务验证载体）

> **一句话**：给 [`app/minimall`](../app/minimall/) 商城装上多用户智能客服 —— 买家在网页里和助手对话，助手查商品 / 查订单 / 加购 / 下单 / 申请退款 / 代付 / 答政策问题，每个动作都走**真实业务链路**（真库、真扣款、真状态机），而 [CharAgent](../CharAgent/README.md) 在这条压力下被逐阶段验证。

---

## 1. 架构：两层服务、三条信任边界

```text
┌──────────┐  session cookie   ┌───────────────────────────────┐
│ 浏览器    │ ────────────────► │ Django :8000                  │
│ 客服页面  │ ◄──────────────── │  BFF 转发 + 内部端点（16 个）  │──► MySQL
└──────────┘  SSE（透传）       └───────────────────────────────┘
                                       ⇅  ②：Django → CharApp（POST /runs）
                                       ⇅  ③：CharApp → 商城内部端点（工具调用）
                                  ┌──────────┴────────────┐
                                  │ CharApp 服务 :1007     │
                                  │ (FastAPI + SSE)        │
                                  └──────────┬────────────┘
                                             │ 进程内调用
                                  ┌──────────┴────────────┐
                                  │ CharAgent 框架（纯库）  │
                                  └───────────────────────┘

① 浏览器 ↔ Django：session cookie —— 全链路唯一的真实认证点（ADR-0001）
② Django ↔ CharApp 服务：X-Internal-Token + X-User-Id（启动 / 取消 / 恢复都走它）
③ CharApp 服务 ↔ 商城内部端点（还是 Django）：同一对头，16 个端点（app/minimall/views_agent.py）
```

- **身份贯穿**：`Django session → user_id → X-User-Id → RunContext.payload["user_id"] → 工具闭包`。`user_id` **永不进入工具的函数签名**，因此永不出现在暴露给模型的 wire schema 里 —— 模型既看不见也改不了它，「诱导模型查他人订单」这条攻击路径天然不存在。
- **依赖方向严格单向**：`CharApp → CharAgent`，框架不 import 任何业务代码（由框架侧一条冒烟测试钉住）。
- **两条脱敏路**：工具事件的载荷在业务层**整条**换成人话才出浏览器（[ADR-0003](docs/adr/0003-tool-events-are-redacted-in-the-business-layer.md)，唯一例外是政策引用的那一段，[ADR-0027](docs/adr/0027-a-cited-passage-may-reach-the-browser.md)）；日志在**写之前**按字段类型打码（[ADR-0019](docs/adr/0019-logs-are-redacted-before-they-are-written.md)）。

---

## 2. 已实现 vs 未实现（L1a → L5 的落地对照）

> 状态：✅ 已实现 · 🟡 部分 · ⬜ 未做。每个阶段都有真机验收记录，明细在各 issue 的收口段。

| 阶段 | 状态 | 落地内容 |
|------|------|---------|
| **L1a** 只读接通 | ✅ | `ToolProvider` 接缝；商城 9 个只读内部端点；助手 9 个只读工具 + CLI（2026-09-19，[issues 01–03](../.scratch/CharApp/issues/01-framework-tool-provider.md)） |
| **L1b** 网页闭环 | ✅ | 框架 `server/`（SSE + 取消）；Django BFF；客服页面（2026-09-21，[issues 04–07](../.scratch/CharApp/issues/04-framework-http-sse-server.md)） |
| **L2** 写路径 + 退款域 | ✅ | hook 拦截点 + 业务护栏；退款域（新建 `RefundRequest`）；**工具集扩到 17 个**；工具事件去字段化（2026-09-22 真机：下单 → 付款 → 退款 → 助手答得出退了 70 元，[issues 08–14](../.scratch/CharApp/issues/08-framework-before-tool-execute.md)） |
| **L2.5** 会话治理 | ✅ | 上下文压缩（账本/视图分离 + 滚动摘要）· 会话记录与水合（重启后历史还在且模型接着上文答）· 前端会话列表与管理动作（2026-09-23，[issues 15–26](../.scratch/CharApp/issues/16-framework-context-compaction.md)） |
| **L3a** 可观测 | ✅ | 工具轨迹落库（`charagent_tool_calls` 第一个生产调用方）· 成本口径 + `trace` 入口 · 日志脱敏（2026-09-25 真机：一次提问两次调用各一行，金额 ¥0.001283 逐档可验算，[issues 27–31](../.scratch/CharApp/issues/27-framework-tool-call-trace-persistence.md)） |
| **L3b** 人工确认 | ✅ | 幂等持久化 · HITL 挂起-恢复 · **助手代付**（本人输密码）· 下单前确认（2026-09-26 真机两条触发面；五条否定断言逐条有结论，[issues 32–38](../.scratch/CharApp/issues/32-framework-idempotency-persistence.md)） |
| **L4** 评估 + A/B | ✅ | 评估集（20 题 6 场景）+ 自动跑分；prompt A/B（v3→v4 成为默认）与工具数量 A/B（2026-09-29，[issues 39–46](../.scratch/CharApp/issues/39-framework-trace-summary-and-run-id.md)，报告在 [`eval/reports/`](eval/reports/)） |
| **L5** RAG + 注入防护 | ✅ | 知识表 + 索引（6 篇政策语料 + 1 篇演示用投毒文档）· 检索工具 + prompt 切到 v5（先查知识库）→ v6（带引用）· **逐句引用**（事件 → BFF → 前端 → 历史，刷新后还能点）· 注入防护四层（2026-10-04，[C08](../.scratch/Charlotte/issues/C08-l5-knowledge-source-and-indexing.md)–[C11](../.scratch/Charlotte/issues/C11-l5-injection-defense.md)） |
| **B 组** 框架加固 | ✅ | 结构化日志 + id 贯穿（[C05](../.scratch/Charlotte/issues/C05-structured-logging-and-ids.md)）· 工具超时 + 取消（[C06](../.scratch/Charlotte/issues/C06-tool-timeout-and-cancel.md)）· 熔断 + 主备切换 + 逐模型记账（[C07](../.scratch/Charlotte/issues/C07-circuit-breaker-and-failover.md) / [C23](../.scratch/Charlotte/issues/C23-failover-per-model-billing.md)–[C25](../.scratch/Charlotte/issues/C25-backup-vendor-dialect.md)） |
| **A 组** 能力扩展 | ✅ | 长期记忆（`remember`/`recall`/`forget`，[C12](../.scratch/Charlotte/issues/C12-long-term-memory-storage.md)/[C13](../.scratch/Charlotte/issues/C13-long-term-memory-tools.md)/[C30](../.scratch/Charlotte/issues/C30-memory-kinds-and-forget.md)）· MCP 两侧（[C14](../.scratch/Charlotte/issues/C14-mcp-both-sides.md)）· 增量渲染 / 边收边发（[C26](../.scratch/Charlotte/issues/C26-delta-seam-and-no-retry-after-output.md)–[C29](../.scratch/Charlotte/issues/C29-stream-flag-adr-and-real-machine.md)） |
| **未做**（明确记录） | ⬜ | 多智能体实现 · 无状态化 + graceful drain · 指标告警 / 灰度回滚 · Docker Compose / 公网部署（判据见 [.scratch/Charlotte/PLAN.md §5](../.scratch/Charlotte/PLAN.md)） |
| **部分** | 🟡 | 记忆召回归还全量（子集化押后，见 C30 末节）· MCP 暴露侧只读 + 单账户（[ADR-0030](docs/adr/0030-mcp-exposes-read-only-tools-for-one-account.md)）· `v5` prompt（防 markdown 强调）押后 · 评估题集没有代付题 · 假商城 `orders/` 返回「已发货」而真机是「待付款」的装置缺陷未修 |

**当前工具集 22 个**：商城 18 个（10 只读 + 8 个打 `writes` 注解的写动作，代付在写动作里）+ 知识检索 1 个 + 长期记忆 3 个（`remember`/`recall`/`forget`）。权威清单在 [`tests/conftest.py` 的 `TOOL_NAMES`](tests/conftest.py)。

---

## 3. 关键决策（每条一行 + ADR）

1. **信任边界写成可兑现的判据**：内部端点直接采信调用方声明的 `X-User-Id`（信任域内转发，唯一验证点是浏览器 ↔ Django 的 session；「什么时候必须还」的判据是**信任域被拆开**，[ADR-0001](docs/adr/0001-internal-endpoint-trusts-declared-user-id.md)）——MCP 暴露侧正是这条判据的一次兑现：信任域拆开了，于是只开只读工具、只代表配置里那一个账户（[ADR-0030](docs/adr/0030-mcp-exposes-read-only-tools-for-one-account.md)），工具重名默认当场报错（[ADR-0031](docs/adr/0031-mcp-tool-name-collisions-fail-fast.md)）。
2. **浏览器 → BFF 用 POST + `fetch` 读流，不用 `EventSource`** —— 需要 POST 带 body、需要在同一条流上区分事件类型（[ADR-0002](docs/adr/0002-browser-to-bff-uses-post-not-eventsource.md)）。
3. **工具事件在业务侧脱敏，不靠前端隐藏** —— 管的是**展示层**；存储层保留原文（轨迹要能查案），两者边界在 [ADR-0003](docs/adr/0003-tool-events-are-redacted-in-the-business-layer.md) 与 [ADR-0016](docs/adr/0016-tool-traces-keep-the-raw-text-because-the-boundary-is-the-browser.md)。
4. **一段对话有两种表示**：账本 append-only、视图每轮现算，给人看的那份**永不压缩**（[ADR-0008](docs/adr/0008-a-conversation-has-two-representations.md)）；压缩触发量投影不量账本（[ADR-0011](docs/adr/0011-estimate-is-calibrated-and-the-trigger-measures-the-view.md)）。
5. **代付的密码走一次性载荷**：与 `user_id` 同一条路（`RunContext.payload`），永不进 wire schema、永不进消息、永不进落库的 `arguments`/`result`；`pay_my_order` 的 schema 里**永远没有** `payment_password` 这个参数（[ADR-0015](docs/adr/0015-payment-password-travels-in-a-one-shot-payload.md)）。
6. **挂起不建审批表**：一次工具调用的状态就是审批单；「一次挂起只挂一条」「拒绝优先于挂起」「判据在 PG 不在内存」三条边界一并兑现（[ADR-0014](docs/adr/0014-a-suspension-has-no-approval-table.md)）。
7. **评估打真模型、跑假商城、只走规则判**：结局四种（跑完 / 截断 / 挂起 / 坏了），只有「跑完」算数；引 LLM-as-judge 会把非确定性引进**判据**里（[ADR-0021](docs/adr/0021-eval-runs-a-fake-mall-and-a-real-model.md)）。**按题裁剪工具必须配一个「拒绝」钩子**：可见集与可调用集必须等价（[ADR-0022](docs/adr/0022-dynamic-scoping-needs-a-reject-hook-too.md)）。
8. **熔断包在重试里、备份换另一家、账按「谁服务记谁」**（[ADR-0025](docs/adr/0025-the-breaker-sits-inside-retry-and-the-backup-is-another-vendor.md)）。

> 共 31 条 ADR，全部在 [`docs/adr/`](docs/adr/)；领域词汇表（会话 / 运行 / 挂起 / 记忆 / 脱敏……的精确定义）在 [`CONTEXT.md`](CONTEXT.md)。

---

## 4. 一行启动

```bash
bash sh/charapp_demo.sh          # 一条命令: 前置检查 → 建表 → 起服务 → 健康检查 → 数据准备 → 开客服页
bash sh/charapp_demo.sh --no-open   # 不开浏览器
```

装置依次检查 MySQL / Redis / Postgres（不活着就给一句能照着做的话）、跑 `alembic upgrade head`、起 Django(8000) 与客服服务(1007)、重建知识库索引、跑幂等的 `demo_prepare`（保证有一笔未付款订单 + 余额下限）。**冷启动实测**：2026-10-03 **14 秒**（服务都没起，到能提问）；2026-10-06 复跑**服务就绪 29 秒 / 整条装置 94 秒**（差额是装置后加的「知识库重建」那一步 —— 要加载本地 bge-m3 并重建 Milvus 集合）。演示剧本与每段兜底动作在 [`sh/charapp_demo.md`](../sh/charapp_demo.md)。

不带浏览器时的等价入口：

```bash
bash sh/charapp_client.sh --user-id 10 -q "我的订单到哪了"   # 命令行, 同一套工具与护栏
```

---

## 5. 测试规模

**CharApp 侧 465 个用例**，全部**离线可跑**：商城走 `respx` 假响应（样本照 `serializers_agent.py` 的真实契约写）、模型走框架的 `MockLLM`（业务侧不抄第二份「假大脑」），不依赖 Django 也不依赖真 API。另有框架侧 **1739 个用例**（见 [CharAgent/README.md §5](../CharAgent/README.md)）。

```bash
cd CharApp && pytest            # 465 个, 零外部依赖
```

真机验收不靠 pytest —— 走 `sh/charapp_demo.sh` 与 `sh/charapp_demo.md` 的剧本（工具调用真实 / HITL 真实 / 副作用真实，60–90 秒一轮）。

---

## 6. 为什么值得看：三个「只有真跑过才知道」的细节

1. **压缩视图隔轮失效** —— 投影与切刀曾被合成一句「不超阈值就原样返回」，而那个「原样」是**全量账本**：于是压过之后每隔一轮视图就失效一次，账本把估算顶回大值、再切一刀（真机八轮模拟：2 压 3 跳、4 压 5 跳，摘要调用白烧一半）。修法是「投影每轮都做，阈值只管要不要再切」（[issue 23](../.scratch/CharApp/issues/23-framework-compaction-view-oscillation.md)）。
2. **估算器坐标不一致** —— 锚估算器标注的坐标是**账本条数**，而那个真实值来自**视图**：同一份内容按账本估高估一倍、按视图估低估 20 倍，触发判据因此时灵时不灵（[issue 26](../.scratch/CharApp/issues/26-framework-compaction-hardening.md)）。
3. **「只裁不拒」是假的** —— L4 做工具数量 A/B 时发现：裁剪工具后模型**仍然调得到**（执行查的是 `self._tool_map`，与当轮那份 `tools` 无关）。只裁不拒的话，两组比的不是「少给」而是「看不见」——A/B 的前提当场失效（[issue 44](../.scratch/CharApp/issues/44-charapp-tool-count-ab.md) / [ADR-0022](docs/adr/0022-dynamic-scoping-needs-a-reject-hook-too.md)）。

这三条的共同点是：**静态读代码看不出来，跑一遍才现形** —— 它们也都被测试或 A/B 装置钉住，不是口头经验。

---

## 7. 已知边界

- **单进程部署前提**：BFF 与服务共用一个进程级 HTTP 客户端（[ADR-0020](docs/adr/0020-the-bff-shares-one-upstream-client.md)）；MCP 暴露侧同样按本地演示设计。
- **MCP 暴露侧的信任边界**：只读工具 + 单个固定 `user_id`（配置里那一个），不做真鉴权（[ADR-0030](docs/adr/0030-mcp-exposes-read-only-tools-for-one-account.md)）。
- **评估覆盖边界**：20 题、6 场景；无 LLM-judge；题集回流一次都没发生过；装置有两处已知缺陷（假商城下单返回状态与真机不一致、题集没有代付题）。
- **两条「核实后不做」**：挂起超时（先要回答「超时了怎么办」）与角色分离（本项目发起方是模型、确认人是买家本人）—— 记录在 [issue 38](../.scratch/CharApp/issues/38-l3b-closeout.md)。
- **记忆召回**：全量返回（每用户 ≤ 50 条），子集化分层演进押后（[C30](../.scratch/Charlotte/issues/C30-memory-kinds-and-forget.md)）。
- **不做 Docker Compose / 公网部署**：面试形式是本地共享桌面演示（[PLAN.md §5](../.scratch/Charlotte/PLAN.md)）。

---

> **最后更新**：2026-10-06（C19 · 四条经历 README）。工具数 / 端点 / 用例数为当天实测。
