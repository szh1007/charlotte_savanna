# CharAgent — 从 0 手写的 AI Agent 运行时

> **一句话**：一个**不依赖任何 LLM 框架**的 agent 运行时 —— agent loop、流式事件总线、断点续跑、重试/熔断、HITL 挂起、日志脱敏全部手写。`pyproject.toml` 里那 8 项依赖就是「零框架依赖」这句话的证据：没有 LangChain / LangGraph / LlamaIndex，删掉任意一项框架立刻有 import 报错。
>
> 它是 [`CharApp`](../CharApp/README.md)（电商智能客服）的运行时底座。框架对业务零知识（不 import 任何 Django / minimall / CharApp 代码），依赖方向严格单向 `CharApp → CharAgent`。

---

## 1. 架构：十五个包，一层门面

```text
                          业务应用 (CharApp)  ── 依赖方向只有这一个: 业务 → 框架
                                │
     ┌──────────────────────────▼───────────────────────────────────────┐
     │ 四个「应用入口 / 需要额外条件」的包 —— 刻意不进根门面:              │
     │   client      CLI 入口 (python -m CharAgent.client)               │
     │   server      HTTP + SSE 服务层      [可选依赖 charagent[server]] │
     │   mcp_client  接入外部 MCP server 工具 [可选依赖 charagent[mcp]]   │
     │   eval        离线跑分 (要真 API key)                              │
     └──────────────────────────┬───────────────────────────────────────┘
                                │  import CharAgent  ← 根门面汇聚下面十一个
     ┌──────────────────────────▼───────────────────────────────────────┐
     │ agent        手写 loop: 并行工具 / 错误自纠错 / 循环防护 / 压缩     │
     │ tool         @tool 装饰器 + schema 生成 + 超时与取消               │
     │ stream       流式事件总线 (九类事件 + seq + 四条不变量)            │
     │ checkpoint   快照协议 + 内存/Redis/Postgres 三实现 + 断点续跑      │
     │ db           六实体数据模型 + alembic 迁移 + 仓储 + 会话记录        │
     │ hooks        六个触发点的注册表 (工具执行前那个可拒绝)             │
     │ model        ChatModel 协议 + httpx 裸调 / openai SDK 双适配器     │
     │ retry        重试退避 + 熔断 failover + 幂等键                     │
     │ redact       日志脱敏 (四条通用规则 + 业务声明的字段路径)          │
     │ structured_logging  一处日志出口 + 打码工序 + 三个 id 贯穿         │
     │ prompt       提示词集中存放与按名加载 (含身份说明的「引用」)       │
     └───────────────────────────────────────────────────────────────────┘
```

| 包 | 一句话 |
|----|--------|
| `agent` | 手写 agent loop —— 一次模型调用如何变成多轮工具循环 |
| `tool` | `@tool` 装饰器、JSON schema 自动生成、可操作错误语义、工具层超时 |
| `stream` | 九类流事件 + seq + 四条状态机不变量；`delta` 只作预览、`final` 才是权威 |
| `checkpoint` | 快照序列化协议 + 三实现（内存 / Redis / Postgres）+ 断点续跑与挂起 |
| `db` | 六实体 + alembic 迁移 + 仓储 + `recorder.py`（会话记录层的第一个调用方） |
| `hooks` | 六个触发点的注册表；`BEFORE_TOOL_EXECUTE` 可以拒绝（HITL 与护栏的落点） |
| `model` | 薄 ChatModel 协议；httpx 裸调与 openai SDK 两个适配器可互换 |
| `retry` | 重试退避 / 熔断三态 / 主备切换 / 幂等键 —— 全部包在模型协议层 |
| `redact` | 框架给规则与协议、业务给字段名单；脱敏发生在写日志**之前** |
| `structured_logging` | 一事件一行 JSON、`thread_id`/`run_id`/`request_id` 三个号贯穿 |
| `prompt` | 提示词按名加载；身份说明按「引用」记进快照（不逐帧抄正文） |

**为什么手写**：`docs/DESIGN.md` 把 agent 工程拆成 70 个难点（#1–70，十四册）—— 这 70 条是设计地图，具体哪几条落地了看下一节的对照表。手写的代价是慢，收益是每个机制都能回答「为什么这么设计、替代方案被否在哪」。

---

## 2. 已实现 vs 已设计未实现

> **先读这一句**：`docs/DESIGN.md` 与 `docs/difficulties/` 十四册是**难点地图**，不是实现清单 —— 它们按「有哪些坑」组织（含大量 P2 设计储备），判断某条做没做**以本表为准**。状态：✅ 已实现 · 🟡 部分 · ⬜ 未做。

| 能力 | 状态 | 一句话 |
|------|------|--------|
| 核心循环 #1-3 / #10-11 | ✅ | 并行工具调用 + 部分失败回填 / 错误自纠错（可操作错误文本）/ 三种软限制 + kill switch / `finish_reason` 六态与截断续写 / reasoning 双通道 |
| 流式事件状态机 #4 | ✅ | 九类事件 + seq + 四条不变量，违反即抛错（`stream/bus.py`）；终局事件恰好一个 |
| 断点续跑 #5 | ✅ | 每 Turn 落一帧、按 `thread_id` 分区；内存 / Redis / Postgres 三实现，能力差异先声明再使用 |
| 上下文压缩 #7 | ✅ | 账本 / 视图分离 + 滚动摘要 + 工具结果截断；摘要失败不切刀，只有上游报超窗口才允许硬截断（[ADR-0008](../CharApp/docs/adr/0008-a-conversation-has-two-representations.md) / [0011](../CharApp/docs/adr/0011-estimate-is-calibrated-and-the-trigger-measures-the-view.md) / [0012](../CharApp/docs/adr/0012-a-failed-summary-must-not-cut-and-only-overflow-may.md)） |
| 重试 / 熔断 / 工具超时 #13-15 | ✅ | `RetryingChatModel(FailoverChatModel(主, 备))`；备份换另一家；工具超时即**中断本次运行**（[ADR-0023](../CharApp/docs/adr/0023-a-tool-timeout-abandons-the-wait-not-the-work.md) / [0024](../CharApp/docs/adr/0024-a-timed-out-tool-interrupts-the-run.md) / [0025](../CharApp/docs/adr/0025-the-breaker-sits-inside-retry-and-the-backup-is-another-vendor.md)） |
| 幂等键 #17 | ✅ | 键是**三列** `(run_id, message_id, tool_call_id)` + PG 存储；真机战绩：同一个恢复请求重放两次，一分钱没多扣、工具一次没多跑 |
| HITL 挂起-恢复 #25 | ✅ | `Decision` 第三值 + `approval_required` 事件 + `POST /runs/{id}/resume`；**挂起不建审批表**（[ADR-0014](../CharApp/docs/adr/0014-a-suspension-has-no-approval-table.md)），判据落 PG，重启也拦得住 |
| 日志脱敏 + 结构化日志 #26 / #38 | ✅ | 脱敏在写之前，按字段类型打码不靠正则碰运气；一处出口 + 三个 id 贯穿，框架自己的异常栈也过同一个出口（[ADR-0019](../CharApp/docs/adr/0019-logs-are-redacted-before-they-are-written.md)） |
| 会话记录与水合 #12 | ✅ | 记录（给人看，永不压缩）与快照（给模型，append-only）是两份；重启后水合读回历史，**绝不重放**工具调用 |
| 测试 #61-63 | ✅ | MockLLM 三形态 + 轨迹断言 + 快照 / 契约测试；三个 checkpoint 实现各有一套契约用例（同一份断言跑三遍） |
| 成本 #34 / #35 | 🟡 | 峰谷计价 + 金额在收尾那一刻算好写死 + 逐模型用量归因（[ADR-0018](../CharApp/docs/adr/0018-run-cost-is-written-at-finish-with-peak-valley-prices.md)）；**模型分级路由 / 语义缓存 / Batch API 未做** |
| 评估 #58 | 🟡 | `eval/` 给协议与跑批器、业务给题与判据；只做**规则判**，LLM-as-judge 刻意不做（[ADR-0021](../CharApp/docs/adr/0021-eval-runs-a-fake-mall-and-a-real-model.md)）；数据飞轮只做了一半（题集回流从未发生过） |
| 长期记忆 #31-33 | 🟡 | `charagent_memories` 表 + `remember` / `recall` / `forget` 工具，`(租户, 用户)` 强制过滤、软删、时间衰减 + 容量淘汰（[C12](../.scratch/Charlotte/issues/C12-long-term-memory-storage.md) / [C13](../.scratch/Charlotte/issues/C13-long-term-memory-tools.md) / [C30](../.scratch/Charlotte/issues/C30-memory-kinds-and-forget.md)）；**语义检索、召回子集化、程序性记忆未做** |
| MCP #50 | 🟡 | 消费侧 `McpToolProvider` + 工具重名 fail fast（[C14](../.scratch/Charlotte/issues/C14-mcp-both-sides.md) / [ADR-0031](../CharApp/docs/adr/0031-mcp-tool-name-collisions-fail-fast.md)）；三类能力只用了 tools。**暴露侧在业务侧**：只开只读工具、且只代表配置里那一个账户（[ADR-0030](../CharApp/docs/adr/0030-mcp-exposes-read-only-tools-for-one-account.md)） |
| 增量渲染 #66 | 🟡 | `answer_delta` 事件 + 前端两档渲染；**吐过字就不重发**（重试与换家都停，[ADR-0029](../CharApp/docs/adr/0029-once-a-delta-is-out-the-request-is-not-resent.md)）；缓存命中 / 模型分级 / 减少工具往返未做 |
| 可观测 #41（一块） | 🟡 | 自写只读 trace 入口 `python -m CharAgent.client.trace <run_id> [--view]`；**不接** OpenTelemetry / Langfuse 那一类（要的是「给个编号就能回看这一次」） |
| 多 agent #42-44 | ⬜ | **明确不做** —— 本项目没有多智能体的必要，改为写原理底稿（[C22](../.scratch/Charlotte/issues/C22-multiagent-notes.md)） |
| 无状态化 / 水平扩展 #64 | ⬜ | **明确不做**（对演示没有实质帮助），改为教学文档（[C21](../.scratch/Charlotte/issues/C21-stateless-and-drain-notes.md)）；单进程假设见 §5 |
| 限流 / 分布式锁 / 队列 #20-22 | ⬜ | 设计储备；目前的闸（熔断）是**进程内**的，跨实例共享属 #21 |
| RAG 本体 #45-49 | ⬜ | 框架层不建 —— 由业务侧落（CharApp 的检索工具 + 注入防护四层，[ADR-0028](../CharApp/docs/adr/0028-injection-defense-is-four-layers.md)） |
| 工具沙箱 / SSRF #24 / #30 | ⬜ | **核实后不做**：落点已修订为「不直连业务库 + 出站目标白名单 + 身份不进工具签名」，三条成立（将来接外部抓取类工具时要重新评估） |
| 指标告警 / 灰度回滚 #39 / #40 | ⬜ | 报告里已有归因列与版本列，做到这一步的性价比不成立 |

---

## 3. 关键决策（每条一行，细节在指针里）

1. **零框架依赖是立身之本**：`pyproject.toml` 的必装依赖只有 8 项且都是框架真正 import 到的；`server` / `pricing` / `mcp` 三组做成**可选** extra —— 不装 web 框架也能用 agent loop，这条纪律由用例守着（`tests/test_root_facade.py` 起子进程验「import 根门面不会拖上 web 栈」）。
2. **加业务能力不许改 `agent/loop.py`**：L1a 定下的硬约束 —— 若加一个业务接入点需要改循环核心，说明接缝设计失败。工具超时 / 熔断 / 记忆 / MCP 四批**纯加法**都没有碰过它；此后改过它的只有确实动到循环语义的那几批（压缩、会话记录、挂起、增量渲染）。
3. **压缩是「投影」不是「删历史」**：账本 append-only 一字不改，每次调用前投影出视图；触发判据量的是**投影**而不是账本（拿账本判的话压完一次就永远超线）。真机抓出过「投影与切刀合成一句」导致每隔一轮视图失效、摘要白烧一半的 bug（[issue 23](../.scratch/CharApp/issues/23-framework-compaction-view-oscillation.md)）。
4. **重试包在模型层、熔断包在重试里、备份换另一家**：嵌套顺序是这一层的要害 —— 闸在里层才看得见每一次物理调用；跳闸的那一跳当场改走备份（默认阈值 3 正好等于重试次数 3，不这样做备份永远等不到出场）；账按「谁服务记谁」（[ADR-0025](../CharApp/docs/adr/0025-the-breaker-sits-inside-retry-and-the-backup-is-another-vendor.md)）。
5. **工具超时是「不再等它」不是「它停了」**：同步工具跑在线程池里掐不掉，日志如实记 `thread_still_running`；2026-10-04 改判为**超时即中断本次运行** —— 结果未知时不把决定权交回模型（[ADR-0024](../CharApp/docs/adr/0024-a-timed-out-tool-interrupts-the-run.md)）。
6. **挂起不建审批表**：一次工具调用的状态就是审批单（`status = needs_approval` + `approved_by`/`approved_at` + 快照里的挂起记录）；「一次挂起只挂一条」「拒绝优先于挂起」「有没有未决挂起看 PG 不看内存」三条边界一并兑现（[ADR-0014](../CharApp/docs/adr/0014-a-suspension-has-no-approval-table.md)）。
7. **日志先脱敏再写**：脱敏是**结构化**的（知道字段是手机号就按手机号打码），不是正则碰运气；访问日志整个关掉（搜索词会走查询串）（[ADR-0019](../CharApp/docs/adr/0019-logs-are-redacted-before-they-are-written.md)）。
8. **测试与评估分两条线**：测试测「代码对不对」（确定性、可 mock、进 pytest），评估测「输出好不好」（非确定性、打真模型、只规则判、落报告人工读）—— 划线划错两边都做不好（[ADR-0021](../CharApp/docs/adr/0021-eval-runs-a-fake-mall-and-a-real-model.md)）。

> 全部 31 条 ADR 在 [`CharApp/docs/adr/`](../CharApp/docs/adr/)（框架级决策与业务级决策放在一起，因为多数决策是两边一起拍板的）；框架自己的设计地图在 [`docs/DESIGN.md`](docs/DESIGN.md)。

---

## 4. 一行启动

```bash
# 在仓库根目录（依赖与 Key 见根 .env）—— 带工具的问答 + 事件流实时打印
python -m CharAgent.client -q "3.5 公里换算成英里是多少"

# 换快照后端 / 断点续跑 / 回看历史
python -m CharAgent.client --backend redis          # 或 memory / postgres
python -m CharAgent.client --resume                 # 从最新一帧接着跑
python -m CharAgent.client --history                # 只打印本会话的快照历史表
```

实测输出（2026-10-06，真模型）：

```text
提问: 3.5 公里换算成英里是多少
[tool_call] convert_length({"value": 3.5, "from_unit": "kilometer", "to_unit": "mile"})
[tool_result] convert_length ok (0.9ms): 3.5 kilometer = 2.1748 mile
[final] 答复就绪 (finish_reason=stop, 3400 tokens, 1.7s)
[完成] 答完了 · 2 轮 · 运行 9d06707f… · 快照 PostgresCheckpointSaver 里 2 帧
```

要作为**包**安装使用（`pip install -e CharAgent/`）也可以；`client` / `server` / `mcp_client` / `eval` 四个包加上对应的 extras 即可。

---

## 5. 测试规模与已知边界

**测试**：**1739 个用例**（`addopts` 默认跑 **1594** 个；其余 145 个按 marker 排除 —— `pg` / `pg_db` / `redis` 需要本机 Postgres / Redis，`integration` / `eval` 要真 API key 与网络）。

```bash
cd CharAgent && pytest                 # 默认 1594 个, 零外部依赖
cd CharAgent && pytest -m pg_db        # db 层 / alembic / 库结构一致性
```

分类：单测为主（loop 的每条不变量、schema 生成、事件序列、压缩估算器）；三实现 checkpoint 的**契约测试**（同一份断言分别跑内存 / Redis / Postgres）；checkpoint 与事件流的**快照测试**（七份 fixture 覆盖 v1→v7 的逐级迁移）；真模型的 `integration` 与整批跑分 `eval`（默认排除）。

**已知边界**（如实写）：

- **单进程假设**：熔断闸是进程内的；CharApp 的 BFF 与服务共用一个进程级 HTTP 客户端，这条链路依赖单进程部署（[ADR-0020](../CharApp/docs/adr/0020-the-bff-shares-one-upstream-client.md)）。
- **记忆的召回是全量返回**：每用户 ≤ 容量上限（50 条），子集化 / 语义检索分三层演进、尚未做（见 [C30](../.scratch/Charlotte/issues/C30-memory-kinds-and-forget.md) 末尾「召回演进建议」）。
- **评估不覆盖过程类质量**：语气、说服力、多轮策略量不了；LLM-as-judge 刻意不引（[ADR-0021](../CharApp/docs/adr/0021-eval-runs-a-fake-mall-and-a-real-model.md)）。
- **`docs/difficulties/` 的十四册是设计笔记**：分册正文不随实现更新，落点看 `docs/DESIGN.md` 各册末尾的「落地」段与本 README §2。
- **Windows 侧的一处实现选择**：Postgres checkpoint 用同步驱动 + `asyncio.to_thread`（psycopg 异步连接在 Windows 默认事件循环上不可用）。

---

> **最后更新**：2026-10-06（C19 · 四条经历 README）。测试数字为当天 `pytest --collect-only` 实测。
