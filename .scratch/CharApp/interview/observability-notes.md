# 可观测（L3a）· 专题底稿

> 本目录一共四份：`INTERVIEW.md`（题典：题 + 五段结构）· `context-compaction-notes.md`（上下文压缩专题）· `estimator-and-trigger.md`（压缩的原理底稿）· **本份**（可观测专题：工具轨迹 / 成本口径 / 日志脱敏 / 起点延迟与竞态）。
> 覆盖阶段：**L3a**，对应 issues **27–31**（2026-09-24 ~ 09-25 落地，09-25 真机验收）。决策记录：ADR-0016 / 0018 / 0019 / 0020。
> 代码锚点：[`CharAgent/agent/loop.py`](../../../CharAgent/agent/loop.py) · [`CharAgent/agent/utils/types.py`](../../../CharAgent/agent/utils/types.py) · [`CharAgent/db/recorder.py`](../../../CharAgent/db/recorder.py) · [`CharAgent/db/cost.py`](../../../CharAgent/db/cost.py) · [`CharAgent/client/trace.py`](../../../CharAgent/client/trace.py) · [`CharAgent/redact/`](../../../CharAgent/redact/) · [`CharApp/minimall/log_redaction.py`](../../../CharApp/minimall/log_redaction.py) · [`app/minimall/views_bff.py`](../../../app/minimall/views_bff.py) · [`templates/minimall/agent.html`](../../../templates/minimall/agent.html)
> 本文里的数字全部来自真机实测或测试计数，出处写在数字旁边。
> **行业侧**（§1）于 2026-09-28 一轮调研抓取，全部取自官方文档 / 官方仓库源码原文，逐条附链接；凡「没找到」的都如实标注。主要来源：OpenTelemetry GenAI semconv 仓库（`open-telemetry/semantic-conventions-genai`）· W3C Trace Context · OWASP Logging Cheat Sheet / ASVS 4.0 / CWE-598 / CWE-532 · NIST SP 800-92 · Microsoft Presidio · Sentry · Datadog · AWS CloudWatch Logs · LangSmith / Langfuse / LiteLLM / Helicone / OpenLLMetry 各家官方文档与仓库 · httpx / httpcore 官方文档与 changelog · MDN · React 官方文档 · Starlette / FastAPI / uvicorn 官方文档与源码。

---

## 0. 五分钟版

### 0.1 一句话

**L3a 只回答一个问题：出问题的时候，能不能查到「当时它看到了什么、花了多少钱、谁把它写进了日志」。**

四片各管一段：

| 片 | 管什么 | 一句话 |
|----|--------|--------|
| **27** | 轨迹 | 把「每一次工具调用」写进库，为此把记录层的写时机从**收尾一次性写**改成**产生即落库** |
| **28** | 钱 | 运行收尾那一刻按当时的价目表把金额与算式算好写死，`trace <run_id>` 只读库 |
| **29** | 痕迹 | 日志**先脱敏再写**；CharApp 的访问日志整个关掉（URL 里会带搜索词） |
| **30** | 起点时间 | BFF 每次都新建 HTTP 客户端（本机 690 ms，花在首字之前）→ 改成进程级共享；前端历史响应的竞态加一道 ticket 闸门 |
| **31** | 收口 | 真机验收五条 + 欠账清点（**每条都有归属**，没有"待定"）+ 文档同步 |

### 0.2 为什么是这四片（不是别的）

这一阶段**没有新增任何插件**，而且这是核实后的结论，不是漏项：

| 要落的东西 | 落在哪 | 为什么不是插件 |
|-----------|--------|--------------|
| 工具调用事实 | `ConversationRecorder`（记录层） | 它是**控制状态**的宿主（挂起那条 `needs_approval` 必须在挂起前落库），而 `fire` 类钩子的异常只记一笔、工具照跑 —— 兜不住 |
| 成本分量 | `charagent_runs` 五列（**早就有了**，ADR-0005 建的） | 写入路径零新代码，折算在收尾那一拍 |
| 日志脱敏 | `Redactor` 协议 | 它是**一个被调用的函数**，不是挂载点 |

> 面试上这句话可以直接说：**「我核实过，这个阶段不该有可观测插件目录。三件事各自落在既有的接缝上。」** 能说出「我没做 X，因为核实后不需要」比「我做了 X」更像工程判断。

### 0.3 真机验收的硬数字（面试可以直接报）

`trace` 打出的一次运行（2026-09-25，浏览器 → Django:8000 → 客服服务:1007 → 真模型 `deepseek-flash`）：

```
run  d425e954e51c463db54a9a25f06300e8
  会话 minimall:10:12885448-... (租户 minimall / 用户 10)
  状态 finished · 模型 deepseek-flash · 提示词 system/v2
  起止 2026-09-25 05:24:10Z → 2026-09-25 05:24:12Z (耗时 1.7s)
  token input 11552 (cache_hit 11136 / cache_miss 416) · output 161 (reasoning 50) · total 11713
  金额 ¥0.001283 (valley)
    └ cache_miss 416 x ¥1/M + cache_hit 11136 x ¥0.02/M + output 161 x ¥4/M

工具调用 (2 次)
  #  工具名          状态       耗时  参数
  1  list_my_orders  succeeded  79ms  {}
      └ 结果: {"count": 1, "page": 1, ...}
  2  get_my_profile  succeeded  65ms  {}
      └ 结果: {"id": 10, "username": "savanna", ...}
```

- **金额可复算**：`804×1/M + 11776×0.02/M + 150×4/M = 0.000804 + 0.00023552 + 0.0006 = 0.00163952` → 六位刻度 `0.001640` ✓
- **档位判对了，而且生效的是节假日那条规则**：那一刻本来也落在两个峰窗（9–12 / 14–18）之外，但那天按中国日历是**中秋假期** —— 两个理由都指向谷价，**真正生效的是后一条**
- **BFF 转发**：690 ~ 970 ms → **2 ~ 16 ms**（六条路径整条调用的墙钟）
- **脱敏**：搜索词打进服务日志后搜 **0 次**、访问日志 **0 行**

### 0.4 这阶段最值钱的三句话

1. **「我为了写一张轨迹表，改了记录层十年来（夸张说法）的写入时机。」** —— 27 的外键逼出「产生即落库」，顺带修掉三件事（见 §4.1）。
2. **「金额我前一天定的是『查询侧现算』，第二天自己推翻了。」** —— 理由是账单按当时那一版价开（ADR-0018），见 §4.2。**能讲清"我改过主意、为什么改"是加分项。**
3. **「脱敏我做了两个接口，而且明确知道哪个是主手段。」** —— `redact_fields`（按字段名，主）vs `redact_text`（按形状，最后一道，且**不假装兜得住**），见 §4.3。

---

## 1. 行业全景：企业级 LLM 可观测长什么样

### 1.1 OpenTelemetry：LLM 可观测的行业标准长什么样

#### 1.1.1 位置与状态（先说清「标准」有多稳）

| 事实 | 出处 |
|------|------|
| GenAI 约定**已不在 opentelemetry.io 主站** —— 迁到了独立仓库 `open-telemetry/semantic-conventions-genai`；主站那个页面实测返回 "Moved" | github.com/open-telemetry/semantic-conventions-genai |
| 文档头部写着 `**Status**: [Development]`；属性/指标表格的 Stability 列**全是 Development** —— **没有一个是 stable 的 GenAI 属性** | 仓库 `docs/gen-ai/gen-ai-agent-spans.md` |

> **这一条面试时要先说**，它决定了后面所有话的分量：**「GenAI 那套约定到今天还是 Development —— 所以拿它对标是『方向对上了』，不是『我没符合标准』。」**

#### 1.1.2 span 层级（名称 + span kind 都是官方原文）

| 操作 | span name（SHOULD） | span kind |
|------|-------------------|-----------|
| 建 agent | `create_agent {gen_ai.agent.name}` | `CLIENT` |
| 调 agent（客户端侧） | `invoke_agent {gen_ai.agent.name}`；无 `agent.name` 时用 `invoke_agent` | `CLIENT` |
| 调 agent（**进程内**） | 同 `invoke_agent` 命名 | **`INTERNAL`** |
| 调 workflow | `invoke_workflow {gen_ai.workflow.name}` | 分 client / internal 两节 |
| **规划阶段** | `plan {gen_ai.agent.name}` | 单列 "plan span" 节 |
| 单次推理 | `{gen_ai.operation.name} {gen_ai.request.model}` | `CLIENT` |
| **工具执行** | **`execute_tool {gen_ai.tool.name}`** | **`INTERNAL`** |

父亲关系有一条原文值得记（讲「层级」时用）：

> `plan` span "represents the decision phase where an agent formulates a strategy before executing it. The LLM call that generates the plan **SHOULD be a child of** the plan span, and the tool or task spans produced from the plan are typically **sibling operations** under the same `invoke_agent` span."

`gen_ai.operation.name` 的 well-known values（全为 Development）：`chat` / `create_agent` / `invoke_agent` / `invoke_workflow` / `plan` / **`execute_tool`** / `embeddings` / `generate_content` / `retrieval` / `fetch_response` / `text_completion` / `create_memory` / `search_memory` / `update_memory` / `upsert_memory` / `delete_memory` / …

#### 1.1.3 工具 span 的属性 —— 与本项目的表**逐列对上**（这一节是面试主菜）

OTel 给工具 span 定义的属性（原文属性名）：

| OTel 属性 | 要求 | 本项目的对应 |
|-----------|------|-------------|
| `gen_ai.tool.name` | **Required** | `charagent_tool_calls.tool_name` |
| `gen_ai.tool.call.id` | 有 | `charagent_tool_calls.tool_call_id` |
| `gen_ai.tool.call.arguments` | 有 | `charagent_tool_calls.arguments`（**原样 JSON**） |
| `gen_ai.tool.call.result` | 有 | `charagent_tool_calls.result` |
| `gen_ai.tool.description` / `gen_ai.tool.type` | 有 | 无（工具描述在装配期，不进轨迹） |

> **这个对照非常值钱**：**我的表结构不是随手设计的 —— 它和 OTel 给工具 span 定的四个属性一一对上**（名字 / 调用编号 / 参数 / 结果）。**面试时可以主动说：「如果要把这张表导出成 OTLP，字段是现成的 —— 四个属性名我都能直接映射，缺的只有 trace_id / span_id 那一层外衣。」**

token 属性（真实属性名，均 Development + Recommended）：

```
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
gen_ai.usage.cache_read.input_tokens        ← 对应我的 cache_hit_tokens
gen_ai.usage.cache_write.input_tokens       ← 我没有这一档 (见 §1.2.2)
gen_ai.usage.reasoning.output_tokens        ← 对应我的 reasoning_tokens
```

> **又是一次逐列对上**：五个 token 属性里**四个**能直接映射到 `charagent_runs` 的五列。**差别只有一处**：`cache_write`（写缓存也要钱，Anthropic 那套）我没有 —— 因为我用的供应商（DeepSeek）不区分「写缓存」这一档。

#### 1.1.4 trace 的数据结构：层级是用「编号」表达的

| 概念 | 规格 |
|------|------|
| `TraceId` | **16 字节**；一个 trace 内所有 span 共享 |
| `SpanId` | **8 字节**；子 span 继承父的 `TraceId` 与 `TraceState` |
| 跨进程传播 | W3C Trace Context 的 `traceparent` 头，语法 `version-format = trace-id "-" parent-id "-" trace-flags"`；`trace-id = 32HEXDIGLC`、`parent-id = 16HEXDIGLC` |

> **对照**：OTel 用 `parent_span_id` 表达父子；**本项目用 `(run_id, message_id, tool_call_id)` 的三列复合主键表达同一件事** —— 一次运行 → 一条 assistant 消息 → 一次调用。**层级是同构的，差别在「谁来传播」（OTel 靠 W3C 头跨进程，我只在单进程内）。**

#### 1.1.5 后端存什么（企业里那一侧的体量）

| 后端 | 原文要点 |
|------|---------|
| **Jaeger** 2.21 | "Cassandra, Elasticsearch, and OpenSearch are the primary supported distributed storage backends. **ClickHouse is supported as an experimental backend behind a feature gate.**" |
| **Grafana Tempo** | "Tempo stores all trace data in **object storage**"（S3/GCS/Azure Blob；本地文件系统仅开发用）；span 落成 **Apache Parquet** 列式块；微服务模式下 Kafka 兼容队列做 WAL |
| **Langfuse** | 自述 "Proudly made with **ClickHouse** open source database"，且「since January 2026 we're part of ClickHouse」 |

> **这一栏面试官很可能追问「那你怎么存」**：我的回答是 **PostgreSQL 两张表**（轨迹 / 运行），因为规模是**单机单用户演示**。**这条差距要主动认**：企业那套是「列式存储 + 对象存储 + 队列」，我这边是「关系库，够用」。

#### 1.1.6 指标：OTel 定义了，我没有（这一整块是缺口）

OTel 的 GenAI 指标清单（原文）：`gen_ai.client.operation.duration` / `gen_ai.client.operation.time_to_first_chunk` / `gen_ai.client.operation.time_per_output_chunk` / `gen_ai.server.time_per_output_token` / `gen_ai.server.time_to_first_token` / `gen_ai.invoke_agent.duration` / **`gen_ai.invoke_agent.tool_calls`** / **`gen_ai.execute_tool.duration`** / …

> **本项目对应的是啥：没有。** `DESIGN.md` 的 **#39（指标 + 告警）** 是 P2、至今未做。**面试时这条要主动说**：**「轨迹（trace）我做了，指标（metrics）没做 —— 这是三支柱里我明确缺的一根。表结构支持聚合（`list_for_run` 之外写 SQL 就行），但『上周工具调用成功率』这种面板我没有。」**

#### 1.1.7 OTel 明确**没有**的东西：钱

对 `gen-ai-agent-spans.md` / `gen-ai-spans.md` / `gen-ai-token-metrics.md` 全文 grep `cost`，**只出现 3 处**（1 处普通上下文，2 处下面这句）：

> token 用量计数器 "are the primary instruments for measuring token consumption ... and serve as **a proxy for cost approximation**"。

**未定义任何金额 / 价格 / 币种属性。** 而且明确写了口径：`cache_read` / `cache_write` / `reasoning` 三个计数器报的是 `input_tokens` / `output_tokens` 的 **subsets**（子集，不是互斥桶）；按操作的直方图 **"should not be used for total usage or cost calculations"**。

> **这一条直接支撑本项目的两个决定**：
> 1. **「reasoning 不重复计价」不是我的发明** —— OTel 原文写的就是 subsets；
> 2. **「金额」这件事标准里根本没有**，所以我自己加 `total_cost` / `total_cost_detail` 两列**不是偏离标准，是在标准之外补一块它不管的事**。OpenLLMetry（traceloop）的官方 issue 也是同一条话：`"Right now we only provide token usage which is basically just a proxy for cost."`

### 1.2 LLM 特有的那一层：token 口径与成本

#### 1.2.1 三家供应商的字段形状（真实字段名）

| 供应商 | 用量字段 | 缓存相关 | 推理相关 |
|--------|---------|---------|---------|
| **OpenAI**（Chat） | `prompt_tokens` / `completion_tokens` / `total_tokens` | `prompt_tokens_details.cached_tokens` / `.cache_write_tokens` / `.audio_tokens` / `.text_tokens` / `.image_tokens` | `completion_tokens_details.reasoning_tokens`（还有 `accepted_prediction_tokens` / `rejected_prediction_tokens`） |
| **OpenAI**（Responses） | `input_tokens` / `output_tokens` / `total_tokens` | `input_tokens_details.cached_tokens`（**required**）/ `.cache_write_tokens`（**required**） | `output_tokens_details.reasoning_tokens`（**required**） |
| **Anthropic** | `input_tokens` / `output_tokens` | `cache_creation_input_tokens` / `cache_read_input_tokens`；`cache_creation.{ephemeral_1h_input_tokens, ephemeral_5m_input_tokens}` | `output_tokens_details`；另有 `service_tier`（`standard` / `priority` / `batch`） |
| **DeepSeek** | 输入拆成两档 | **`prompt_cache_hit_tokens`** / **`prompt_cache_miss_tokens`** | —— |

> **DeepSeek 那两个字段就是本项目三档价目表的由来**：它的定价页把「1M INPUT TOKENS (CACHE HIT)」「1M INPUT TOKENS (CACHE MISS)」「1M OUTPUT TOKENS」分列，并区分**标准时段与错峰时段**（错峰为折扣价）。**我的 `cache_miss` / `cache_hit` / `output` 三档 + `peak` / `valley` 两套价，就是照这张表来的。**

**OpenAI 与 Anthropic 在缓存上报上不一样**（这条下面还要用）：

> LiteLLM 官方对账文档原文：**"OpenAI: Cache read tokens are typically included inside the reported input token count. Anthropic: Cache read tokens are often reported separately from non-cached input tokens."**

#### 1.2.2 「inclusive vs exclusive」—— 一个行业共识的坑，也是我那段代码的由来

**Langfuse 官方文档**把这件事讲得最清楚，原文：

> "`usage_details` 的每个 key 是**互斥桶**（`input` 不含 `input_*`，`output` 不含 `output_*`），`total` 是求和不是桶。"
>
> **"Some provider counts are inclusive. For example, OpenAI input counts include cached tokens. Inclusive counts must be converted into exclusive buckets before they are stored."**

它给的换算例子：

```
上游给: prompt_tokens: 17903, cached_tokens: 17817
存成:   input: 86  +  input_cached_tokens: 17817
```

**Anthropic 的官方公式是同一件事的另一种写法**：

> `total_input_tokens = cache_read_input_tokens + cache_creation_input_tokens + input_tokens`（且 `input_tokens` 只含「最后一个 cache breakpoint 之后」的 token）

**本项目对应的那段代码**（§2.2.5 的 `_fill_missing_tier`）：

```python
derived = total - other          # 未命中 = 输入总量 - 命中
```

> **这是全套材料里最值钱的一次对照**：**我写的「上游没报未命中那一档时，用输入总量减命中来补」不是自作聪明 —— 它是行业里公认必须做的一步换算**，Langfuse 的原文就叫它 "convert inclusive counts into exclusive buckets"，理由与我写的注释一样（OpenAI 系只给 `cached_tokens`）。**面试时直接说这个术语：inclusive → exclusive 桶换算。**

#### 1.2.3 「reasoning 是输出的子集」有三重印证

| 来源 | 原话 |
|------|------|
| **OTel** | `cache_read` / `cache_write` / `reasoning` 三个计数器报的是 `input_tokens` / `output_tokens` 的 **subsets** |
| **OpenAI 字段层级** | `reasoning_tokens` 在 `completion_tokens_details` **之下** —— 这个层级在 schema 上就是 `completion_tokens` 的分解 |
| **DeepAgents / Langfuse 的实务口径** | reasoning 模型（o1 系）**不能靠 tokenizer 估成本，必须上送 usage** —— 也就是说这个分量是「另外报上来的明细」，不是另算一笔 |

> 本项目那条验收（§2.2.5）的三条证据与它们同源。**面试时可以补一句：「这个坑我不知道，是我核出来的 —— 三条证据对着看才敢下结论，因为偏高和偏低都不会报错。」**

#### 1.2.4 价目表放哪：四家的做法

| 工具 | 价目表在哪 | 形状 | 值得注意的 |
|------|-----------|------|-----------|
| **LiteLLM** | 仓库文件 **`model_prices_and_context_window.json`**（官方叫 "model cost map"），**不硬编码在代码里** | 计价 key：`input_cost_per_token` / `output_cost_per_token` / `cache_read_input_token_cost` / `cache_creation_input_token_cost`，另有档位与模态变体（`*_priority` / `*_flex` / `*_batches` / `output_cost_per_reasoning_token` / `input_cost_per_audio_token` / `search_context_cost_per_query` …） | 官方要求「Keep Pricing Data Updated — Sync model pricing data from GitHub」 |
| **Langfuse** | 模块维护的 `default-model-prices.json` + **每日自动审计 workflow**（按官方来源核对价格/档位/usage key）；用户自定义模型优先 | 模型用 `match_pattern`（**正则**）匹配 generation 的 `model`；**pricing tiers** 带 Name / Priority / **Conditions** / Prices，条件来源可以是 usage details（正则匹配 key 后求和，配 `gt/gte/lt/lte/eq/neq`） | 例：Claude Sonnet 的 Large Context tier 条件就是 `input > 200000` —— **同一模型按用量分档定价，靠条件表达** |
| **LangSmith** | "model pricing map"（配置项） | 每次 run 的 `usage_metadata`：`{"input_tokens": 27, "output_tokens": 13, "total_tokens": 40, "input_token_details": {"cache_read": 10}}` | 带 **Model Activation Date**：`"The date from which the pricing is applicable. Only runs after this date will apply this model price."` |
| **Helicone** | 两条路：**网关**用 Model Registry v2 精确算（"100% Accurate"）；**非网关**用开源 cost repository（"pricing for 300+ models"）**估算** | —— | 官方自己把两条路的准确度分开标注 |

**LangSmith 的计费算例**（原文，展示缓存单独计价且**从输入总量里减掉**）：

```python
input_cost = 5 * 1e-6 + (20 - 5) * 2e-6   # 3.5e-5   ← 20 总量里前 5 个是 cache read
output_cost = 10 * 3e-6                   # 3e-5
total_cost = input_cost + output_cost     # 6.5e-5
```

> **注意 `(20 - 5)`**：这就是 §1.2.2 那个换算的另一种写法。**四家里有三家都在做同一件事** —— 说明「三档桶必须互斥」是这条链路的硬约束。

#### 1.2.5 历史价格怎么办：**三家官方都选择「不回填」**

这一节直接给 ADR-0018 提供了行业背书：

| 来源 | 原文 |
|------|------|
| **LangSmith**（最直接） | **"LangSmith does not reflect updates to the model pricing map in the costs for traces already logged. Backfilling model pricing changes is not supported."** |
| **Langfuse** | "Because **inferred costs are calculated at ingestion time**, updated defaults apply **only to new generations**."（排查清单里又重复了一遍：`"Model definition changes only apply to new generations."`） |
| **LiteLLM** | 价格表要手动/定时从 GitHub 同步，且**对账比率会因价格变更而偏离**，直到 map 追上 |
| **供应商侧** | 价格页本身带生效/截止说明（OpenAI 页面标注 "promotional pricing is available at least through November 21, 2026" / "Billing for ... begins on October 5, 2026"；DeepSeek 标错峰折扣） |

> **这是本阶段最有力的一次「我不是拍脑袋」**：ADR-0018 决定「金额在收尾那一刻算好写死、之后不重算」，当时被批评过「这样历史金额就不可重估了」。**现在能答：LangSmith 官方原话就是 "Backfilling model pricing changes is not supported"，Langfuse 是 "only to new generations"，LiteLLM 是靠对账比率暴露偏离 —— 三家的口径与我一致。** 而 LangSmith 的 **Model Activation Date** 与我的「按运行的**开始时刻**判峰谷」是同一个手法：**把「用哪一版价」绑在时间上。**

### 1.3 「这次运行花了多少钱」落在哪一层

#### 1.3.1 三个落点（本项目在第三个）

| 层次 | 谁在那 | 特点 |
|------|-------|------|
| **网关 / 代理层** | **LiteLLM**（每次请求写一行 `LiteLLM_SpendLogs`，字段含 `spend` / `total_tokens` / `prompt_tokens` / `completion_tokens` / `model_group` / `api_base` / `end_user` / `request_tags` / `team_id` / `user`；响应头返回 `x-litellm-response-cost`）· **Helicone**（网关侧 "100% Accurate"） | **一次接入、全公司受益**；能按 key / user / team 归因 |
| **可观测平台层** | **Langfuse**（摄入时算成 USD 存 `cost_details`，按 usage type 拆开）· **LangSmith**（run 落库时按 pricing map + Activation Date 算） | 与 trace 天然在一起，方便按 run 看 |
| **应用层** | **本项目**（记录员在收尾那一刻算好写进 `charagent_runs` 两列） | 只有一条链路时最直接；金额与账单口径绑得最紧 |

**LiteLLM 有一条对本项目特别有启发的设计**：有 usage 但价格为 0 的请求会被**显式标出** —— 日志一行 `WARNING`（`pricing entry '<model_id>' has no input_cost_per_token, output_cost_per_token`）+ **Prometheus 计数器 `litellm_zero_cost_requests_total`，label 含 `reason`**（`missing_pricing_key` / `pricing_not_applied` / `cost_calculation_error`）。

> **这与本项目「算不出来要给原因、绝不报 0」是同一条纪律**，差别只在形态：**LiteLLM 把它做成了指标（可告警），我做成了列里的 `CostGap`（可查询）**。**面试到这里可以主动承认**：「同一条纪律，它做成了指标能告警，我做成了明细能查询 —— 我的那一半缺一个告警出口。」

#### 1.3.2 对账：LiteLLM 的 `spend_capture_rate`（本项目缺的那一层）

LiteLLM 官方文档里有一个**每日对账 job**，公式原文：

> `capture_rate = captured_spend / provider_spend`
>
> "A rate of 1.0 means every dollar the provider billed went through LiteLLM and was priced. A rate under 1.0 means requests are reaching the provider outside LiteLLM (direct API keys, another gateway) or LiteLLM's cost tracking is dropping spend."

- **一侧**是 LiteLLM 自己的 `LiteLLM_DailyUserSpend`；
- **另一侧**是**厂商账单 API**（OpenAI 的 `GET /v1/organization/costs`，`bucket_width=1d`，Admin key）；
- 每天 01:15 UTC 跑，`lookback_days` 默认 7，**阈值默认 0.9**，指标 `litellm_spend_capture_rate`，告警类型 `failed_tracking_spend`；
- 目前**只接了 OpenAI**（"Other providers (Anthropic, Azure, Bedrock, Vertex AI) are not wired yet"）；
- 文档明确价格变更会让比率失真：`"a price change on either side moves the rate until the map catches up."`

**厂商侧也确实提供了对账入口**：

| 厂商 | 接口 | 用途原文 |
|------|------|---------|
| OpenAI | `GET /organization/costs` → `amount{currency, value}` / `line_item` / `num_model_requests` | —— |
| Anthropic | `/v1/organizations/usage_report/messages`（token 维度：uncached input / cached input / cache creation / output）+ `/v1/organizations/cost_report`（仅 `1d`，USD，以「lowest units (cents)」的十进制字符串返回） | 文档把用途直写为 **"Cost reconciliation: Match internal records with Anthropic billing for finance and accounting teams"**；并注明 `Priority Tier costs are not available in the cost endpoint` |

**LiteLLM 还给了一份人工对账配方**（值得记）：固定时间窗 → 确认无绕过流量 → **逐 token 类目对比**（requests / input / output / **cache read / cache write**）→ ~10% 内视为边界与舍入，超出则分「摄入路径」与「价格表过期」两条查。

> **这一节就是本项目那条验收的边界**：issue 31 明写着「本收口**没有去拉供应商账单**（手上没有那一侧的入口），能验的是**算式与单价**」。**面试时这条要主动说，而且要给出企业里的位置**：**「我知道对账长什么样 —— 一个 capture rate、一个每日 job、一个厂商账单 API。我没做，因为我没有那张 Admin key，而且我的验收标准是「可复算」而不是「与账单一致」。** 这两件事的差距我说得清。」**

### 1.4 日志脱敏：行业的一手规范与实现

#### 1.4.1 规范侧：什么不该进日志（OWASP 的原文列表）

[OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) 的导语原文：

> "The following should usually not be recorded directly in the logs, but instead should be **removed, masked, sanitized, hashed, or encrypted**:"

列表里与本项目直接相关的几条：

- Session identification values (consider **replacing with a hashed value**)
- Sensitive personal data and some forms of PII
- Authentication passwords
- Bank account or payment card holder data
- Information a user has opted out of

另一组「可能需要特殊处理」的：File paths / Internal network names and addresses / **Non sensitive personal data (personal names, telephone numbers, email addresses)**。

> **这句话的最后一个分句很关键**：电子邮件与电话号码在 OWASP 的口径里算「非敏感个人数据」，但**仍然要特殊处理**。这正是本项目把 `phone` / `email` 做成**规则打码**（留头留尾）而不是"不打"的依据。

脱敏手法原文：`"de-identification techniques such as deletion, scrambling or pseudonymization of direct and indirect identifiers"` —— 注意 **deletion** 也在列（对应本项目的 `WIPE`）。

后置脱敏：`"In some systems, sanitization can be undertaken post log collection, and prior to log display."` —— **本项目的选择是相反的（写之前脱敏）**，理由见 §2.3 与 ADR-0019。

#### 1.4.2 规范侧：URL 里带敏感数据（这条正中本项目的靶心）

[OWASP: Information exposure through query strings in URL](https://owasp.org/www-community/vulnerabilities/Information_exposure_through_query_strings_in_url) 原文：

> "Information exposure through query strings in URL is when sensitive data is passed to parameters in the URL... **Simply using HTTPS does not resolve this vulnerability.**"

暴露面（HTTP 与 HTTPS **都是**）：`Referer Header` / `Web Logs` / `Shared Systems` / `Browser History` / `Browser Cache` / `Shoulder Surfing`。

[CWE-598](https://cwe.mitre.org/data/definitions/598.html) 的 Mitigation 原文：

> "When sending sensitive information, only include it in the **request body or request headers** instead of the query string. This may require avoiding use of GET requests."

> **这条就是本项目「搜索词走请求体」那行前端注释的规范依据**，也是 issue 29 关掉 uvicorn access log 的依据。面试时可以说：**「我知道这条有 CWE 编号 —— CWE-598，官方 Mitigation 就是把它挪进 request body。」**

另一条相关的：[CWE-532](https://cwe.mitre.org/data/definitions/532.html)（敏感信息写入日志文件），Mitigation 里有 `"Remove debug log files before deploying the application into production."` 与 `"Adjust configurations appropriately when software is transitioned from a debug state to production."`

#### 1.4.3 规范侧：「演示开关」为什么是反模式

[OWASP ASVS 4.0 V14](https://raw.githubusercontent.com/OWASP/ASVS/master/4.0/en/0x22-V14-Config.md)：

- V14.3 标题即 **"Unintended Security Disclosure"**；
- **14.3.2**（L1，CWE-497）：`"Verify that web or application server and application framework debug modes are disabled in production to eliminate debug features, developer consoles, and unintended security disclosures."`
- **14.2.2**：所有 `"unneeded features, documentation, sample applications and configurations"` 必须移除 —— 「演示用」的开关正落在这条射程内；
- **14.4.6**：需要 `Referrer-Policy` 响应头，避免 URL 里的敏感信息经 Referer 泄漏给不可信方。

> 本项目 ADR-0003 与 ADR-0019 两次否掉「演示时打开敏感数据」的开关，依据就在这里。**面试时可以直说：这不是我的洁癖，是 ASVS 14.2.2 / 14.3.2 点名的东西。**

**一个真实事故**（说明「调试期的日志」为什么危险）：Twitter 2018 官方说明原文 ——

> "Due to a bug, passwords were written to an internal log before completing the hashing process. We found this error ourselves, removed the passwords, and are working on plans to prevent this bug from happening again."

**还有一条容易被忽略的**：NIST SP 800-92 §2.3.2 指出，日志里的敏感信息风险**不止于外部攻击者** ——

> "This could expose the information to staff members that are analyzing data or administering the recording systems."

#### 1.4.4 实现侧：四家有代表性的做法

| 产品 | 脱敏发生在哪 | 机制 | 值得注意的取舍 |
|------|------------|------|--------------|
| **Sentry** | **SDK 端**（客户端），另有 Relay 服务端 | `EventScrubber(denylist=..., pii_denylist=...)`；匹配逻辑是**字段名小写比对**（`k.lower() in self.denylist`）；`DEFAULT_DENYLIST` 约 30 项（`password` / `secret` / `api_key` / `token` / `authorization` / `cookie` / `x_forwarded_for` …） | ① `send_default_pii` 默认 **None**（默认不收集）；② 默认**不递归**遍历嵌套结构 —— 原文给的代价是 `"for performance reasons"`，要 `recursive=True` 才递归；③ `before_send` 留给用户手工剥离 |
| **Datadog** | **两层**：Agent 端 + 平台端 | Agent：`mask_sequences` log_processing_rules + `replace_placeholder`（7.17 起可引用捕获组 `$1`）；平台：**Sensitive Data Scanner**（logs / APM / RUM / Events 等），动作有 Redact / Partially redact / Hash / Mask | 明确区分执行位置：「In the cloud」（数据离开你的环境后扫）与「**In your environment**」（Observability Pipelines，离开本地前处理） |
| **AWS CloudWatch Logs** | **摄入时**（ingest） | data protection policy：命中 managed / custom data identifier 的数据**在所有出口被遮蔽**（Logs Insights、metric filters、subscription filters），只有带 `logs:Unmask` IAM 权限的人能看原文 | ① 检测依赖上下文关键词（原文：`"For some types of managed data identifiers, the detection depends on also finding certain keywords in proximity"`）；② **自定义标识符被硬性限制**：每个策略最多 10 个、单条 regex ≤ 200 字符 |
| **Microsoft Presidio** | 库（两段式） | **Analyzer**（`RecognizerRegistry` + `NlpEngine` + `ContextAwareEnhancer`，regex 与 NER 并用，输出 `RecognizerResult{entity_type, score, start, end}`）→ **Anonymizer**（operator：replace / redact / hash / mask / encrypt / keep；默认 replace 产出 `<ENTITY_TYPE>`） | `hash` operator 自 v2.2.361 起**默认随机 salt**（同一值两次哈希不同）—— 即默认**不可做关联分析**，要关联得显式传 salt |

#### 1.4.5 结构化日志：`request_id` 怎么贯穿

| 做法 | 官方口径 |
|------|---------|
| **structlog processor 链** | 每个 processor 收到 `(logger, method_name, event_dict)`，返回值传给下一个；**只有最后一个（renderer）的返回值被真正使用**。→ **脱敏 processor 必须排在 renderer 之前**才有意义 |
| **contextvars 关联** | structlog 官方建议把 `structlog.contextvars.merge_contextvars()` 作为**第一个** processor；Python 官方原文：`"Context managers that have state should use Context Variables instead of threading.local() to prevent their state from bleeding to other code unexpectedly."` |
| **标准库 logging 的 Filter** | [Logging Cookbook](https://docs.python.org/3/howto/logging-cookbook.html) 原文：`"Filter instances are allowed to modify the LogRecords passed to them, including adding additional attributes"` —— 这是「不换框架也能加 request_id」的路 |
| **OpenTelemetry 的 log signal** | 日志数据模型里 `TraceId` / `SpanId` 都是 **optional** 字段；Python 侧 autinstrumentation 注入 `otelTraceID` / `otelSpanID` 等键，默认 format 里带 `[trace_id=%(otelTraceID)s span_id=%(otelSpanID)s]`。**有个坑**：它靠 `logging.basicConfig()` 生效，而 **`basicConfig` 只有第一次调用有效** —— 集成启用前若已被调用过，日志就不带 trace 上下文 |

**一个「没找到」要如实说**：structlog 官方文档里**没有**专门的「脱敏 / mask secrets」processor 章节（在该域内检索 redact / mask / secrets 无结果）。也就是说，**「日志脱敏」在生产里普遍是自己写一个 processor / filter，而不是框架给你的现成件** —— 这一点对本项目的「手写 `Redactor`」是个正面支撑。

#### 1.4.6 「字段级脱敏 vs 正则脱敏」：一句要小心的话

**没找到任何一手文档明确论证「字段级优于正则」**（OWASP / NIST / Presidio / Sentry / Datadog / AWS / OTel 全查过；相关论述只出现在厂商博客）。

**能引的是一手事实**：

| 事实 | 出处 |
|------|------|
| Sentry 的默认 scrubbing 是**按 key 名**比对 denylist，不是按值匹配正则 | `sentry_sdk/scrubber.py` 的 `k.lower() in self.denylist` |
| 同一份文档承认字段级方案的**代价**：默认不递归（性能） | Sentry sensitive-data 文档 |
| AWS 把检测做成两档：managed identifier（含关键词邻近判定）vs **custom identifier（纯正则，且被硬限 ≤10 条 / ≤200 字符）** | CloudWatch custom data identifiers 文档 |
| OTel 明确区分结构与非结构：「结构化」意味着 `"well-defined typed fields that downstream processing can reliably depend on"` | OTel logs 概念页 |
| Presidio 的 Analyzer **不是只靠正则**：regex + NER + `ContextAwareEnhancer` 按上下文词提置信度 | Presidio Analyzer 文档 |

> **所以面试时的正确说法**是：**「『按字段打比按正则靠得住』这句话我不引用规范（我没找到一手依据），我引用的是四家的实现事实 + 我自己的判据：知道它是什么，就按它是什么打。而正则那条我明确承认是最后一道。」** —— 这个区分本身就是加分项：**不把二级来源的话当规范说。**

### 1.5 进程级 HTTP 客户端：httpx 官方的口径

#### 1.5.1 该不该复用（官方明确说该）

[httpx 官方 Clients 文档](https://www.python-httpx.org/advanced/clients/) 原文：

> "If you do anything more than experimentation, one-off scripts, or prototypes, then you should use a `Client` instance."
>
> "When you make requests using the top-level API... HTTPX has to establish a new connection for every single request (connections are not reused)."
>
> "a `Client` instance uses HTTP connection pooling... **This can bring significant performance improvements** compared to using the top-level API, including: Reduced latency across requests (no handshaking). Reduced CPU usage and round-trips. Reduced network congestion."

**而且官方点名了「全局单例」是正当写法**（async 文档，反面例子那一段）：

> "make sure you're not instantiating multiple client instances — for example by using `async with` inside a "hot loop". This can be achieved either by having a single scoped client that's passed throughout wherever it's needed, or by **having a single global client instance**."

> **这两段是对本项目改造的官方背书**：原写法（每次请求 `with httpx.Client(...)`）正是官方说的 "prototypes" 那一档；而"全局单例"是官方列出的两条正当写法之一。

#### 1.5.2 怎么关：官方给的两条路，**没有 `atexit`**

httpx 官方文档里关闭客户端的写法只有两种：

| 写法 | 原文 |
|------|------|
| 上下文管理器 | `"The recommended way to use a Client is as a context manager. This will ensure that connections are properly cleaned up when leaving the with block"` |
| 显式关闭 | `"Alternatively, you can explicitly close the connection pool without block-usage using `.close()`"` |

**一个必须如实说的检索结果**：**httpx 官方文档从未提到 `atexit`**（对发布包 `httpx 0.28.1` 全量源码 grep `atexit` 也是 **0 命中**）。

**与它最接近的官方说法在 httpcore 文档**：

> "Working with a single global instance isn't a bad idea for many use case, since the connection pool will automatically be closed when the `__del__` method is called on it"
>
> "The connection pool will automatically be closed when it is garbage collected, or when the Python interpreter exits."

> **所以本项目的 `atexit.register(_CLIENT.close)` 不在 httpx 的文档口径里** —— 它是「显式关闭 + 不依赖 GC 时机」的一次工程选择。**这条要主动讲，不要装作它是官方推荐**：官方给的兜底是「解释器退出 / GC 时自动关」，而 `atexit` 让「关」这件事**确定地发生在一个已知的时刻**，代价是多一行代码、且在多 worker 部署下不可靠（这正是本文 §2.4 里那条前提）。

#### 1.5.3 连接额度的默认值（本项目的选择与默认**不一样**）

[Resource limits 文档](https://www.python-httpx.org/advanced/resource-limits/) 原文默认值：

| 参数 | 官方默认 | 本项目 | 差异 |
|------|---------|--------|------|
| `max_connections` | **100** | **10** | 收紧 10 倍 |
| `max_keepalive_connections` | **20** | **10** | 收紧 2 倍 |
| `keepalive_expiry` | **5** 秒 | 未改（5 秒） | 一致 |

语义（httpcore 文档原文）：`max_connections` —— `"Any attempt to send a request on a pool that would exceed this amount will block until a connection is available."`

> **面试时这句话要能说清**：**「我知道官方默认是 100/20，我调到 10/10 是因为并发上界本来就小（页面同一时刻只跑一个运行），额度真被占满时会『阻塞等 pool 超时』——所以我把上限压到自己能解释的量级，而不是留着 100 却说不清谁会用到它。」**

#### 1.5.4 `trust_env` 默认 True：那条系统代理是怎么来的

[Environment variables 文档](https://www.python-httpx.org/environment_variables/) 原文：

> "Environment variables are used by default. To ignore environment variables, **`trust_env` has to be set `False`**."

API 签名里 `trust_env=True` 是默认值；受它影响的变量包括 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / **`NO_PROXY`**（`"disables the proxy for specific urls"`）与 `SSL_CERT_FILE` / `SSL_CERT_DIR`。

> **这就是 issue 31 欠账表第 9 条（「BFF 的流量绕一跳系统代理」）的根因**：不是共享客户端引入的，而是 `trust_env` 默认读环境变量的结果。官方给的两条修法也正好是欠账表里写的那两条：`trust_env=False`，或把本机地址加进 `NO_PROXY`。

#### 1.5.5 生命周期该挂在哪：Starlette/FastAPI 有官方钩子，Django 这一侧没有

[FastAPI lifespan 文档](https://fastapi.tiangolo.com/advanced/events/) 原文：

> "This can be very useful for setting up resources that you need to use for the whole app, and that are shared among requests, and/or that you need to clean up afterwards. For example, **a database connection pool**, or loading a shared machine learning model."

[Starlette lifespan 文档](https://starlette.dev/lifespan/) 给的官方示例**就是进程级 httpx 客户端**：

```python
# Starlette 官方示例（Lifespan State 一节）, 原样引用
State = TypedDict("State", {"http_client": httpx.AsyncClient})

@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient() as client:
        yield {"http_client": client}       # 请求内: request.state.http_client

app = Starlette(lifespan=lifespan, routes=[Route("/", homepage)])
```

两条值得记住的细节：`"Starlette will not start serving any incoming requests until the lifespan has been run."`；`"If you provide a lifespan parameter, startup and shutdown event handlers will no longer be called."`

> **这就是本项目那条「Django 没有可靠的进程退出钩子」的对照面**：FastAPI / Starlette 有**官方的 lifespan 钩子**，进程级 HTTP 客户端是它的**教科书用例**；而 Django 这一侧没有等价的钩子（`runserver` 不发 lifespan 事件），所以才需要 `atexit` 这个替代品。**这句话在面试里比「我用了个单例」值钱得多**：它说明我知道**为什么这里需要绕一下**。

#### 1.5.6 「stale connection」：机制在源码里，文档里查不到

**如实记一条检索结果**：**httpx 与 httpcore 的文档都没有专门讲「对端断开后池里那条连接怎么办」** —— 依据只在**源码与 changelog** 里。

源码（`httpcore/_sync/http11.py` 的 `has_expired`）：

```python
# If the HTTP connection is idle but the socket is readable, then the
# only valid state is that the socket is about to return b"", indicating
# a server-initiated disconnect.
server_disconnected = (
    self._state == HTTPConnectionState.IDLE
    and self._network_stream.get_extra_info("is_readable")
)
return keepalive_expired or server_disconnected
```

探测实现（`httpcore/_utils.py::is_socket_readable`）的 docstring 原文：

> "Return whether a socket, as identfied by its file descriptor, is readable. **"A socket is readable" means that the read buffer isn't empty**, i.e. that calling .recv() on it would immediately return some data."
>
> 注释：`"we want check for readability without actually attempting to read, because we don't want to block forever if it's not readable."` —— 实现用零超时的 `select` / `poll`。

**changelog 里能看到这个问题被反复修**：

| 版本 | 条目 |
|------|------|
| httpx 0.6.8 | `"Check for disconnections when searching for an available connection in ConnectionPool.keepalive_connections"` |
| httpcore 0.14.2 | `"Failed connections no longer remain in the pool."` |
| httpcore 0.14.3 | `"Fix race condition when removing closed connections from the pool."` |
| httpcore 0.12.3 | `"Tweak detection of dropped connections, resolving an issue with open files limits on Linux."` |

**还有一条对本项目「不做重发」的支撑**：`HTTPTransport(retries=N)` 的官方语义是 —— `"The maximum number of retries when trying to establish a connection."` —— 也就是**只覆盖「建连阶段」**，覆盖不了「连接已建好、请求发出去之后才断」那一瞬。**httpx 官方文档也没有承诺对 `server disconnected` 自动重试。**

> **面试金句**：「我一开始想加一次重发，后来查清楚了两件事：一是 httpcore 自己就会认死连接（`is_readable` 那段），二是 httpx 的 `retries` 参数语义是**建连时的重试**，覆盖不了我遇到的那一瞬。**所以我不指望官方重试、也没自己加 —— 量过之后判定不划算。**」

### 1.6 前端请求竞态：官方的两种口径

#### 1.6.1 取消请求：`AbortController`

[MDN AbortController](https://developer.mozilla.org/en-US/docs/Web/API/AbortController)：`"represents a controller object that allows you to abort one or more Web requests as and when desired."`

[MDN Using Fetch — Canceling a request](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch)：`"To cancel the request, call the controller's abort() method. The fetch() call will reject the promise with an AbortError exception."`

**两条使用限制**（都很容易被面试问到）：

1. **信号只能用一次**：`"An AbortSignal can only be used once. After it is aborted, any fetch call using the same signal will be immediately rejected."` → 每次请求要新建 controller；
2. **取消不是「错误」**：[Chrome 官方博客 Abortable fetch](https://developer.chrome.com/blog/abortable-fetch/) 原文：`"You don't often want to show an error message if the user aborted the operation, as it isn't an "error" if you successfully do what the user asked."` → 调用方要按 `err.name !== 'AbortError'` 区分，否则用户会看到一条本不该出现的报错。

#### 1.6.2 「忽略结果」也是官方口径（这条正好对上本项目的选择）

**React 官方文档把两种做法并列写出来了**（[Synchronizing with Effects → Fetching data](https://react.dev/learn/synchronizing-with-effects#fetching-data)）：

> "**If your Effect fetches something, the cleanup function should either abort the fetch or ignore its result:**"
>
> "You can't "undo" a network request that already happened, but your cleanup function should ensure that the fetch that's not relevant anymore does not keep affecting your application. If the `userId` changes from `'Alice'` to `'Bob'`, cleanup ensures that the `'Alice'` response is **ignored** even if it arrives after `'Bob'`."

官方示例就是「忽略」那一支（一个 `let ignore = false` 的标志位）。React 还**给这个 bug 定了名**：

> "Bugs like this are called **race conditions** because two asynchronous operations are "racing" with each other, and they might arrive in an unexpected order."

> **这条对本项目太重要了**：前端那道 `historyRequests` ticket 闸门属于官方并列的两条路里的**「忽略结果」**那一支。**面试时可以直接引 React 的原文说「abort 或 ignore 两条都是官方口径，我选 ignore，因为保留旧响应的成本极低（一次 JSON），而 abort 要多一条错误分支（还要处理 `AbortError` 不算错误这件事）」。**

**业界其他口径**（都指向同一件事）：

| 来源 | 原文/事实 |
|------|----------|
| TanStack Query（Query Cancellation） | 给每个 query function 一个 `AbortSignal`，但 `"By default, queries that unmount or become unused before their promises are resolved are **not cancelled**."` —— 默认**不取消**，要显式消费 signal 才会取消 |
| axios README | `"Since v0.22.0, Axios supports AbortController"`；老的 `CancelToken` `"is deprecated since v0.22.0 and should not be used in new projects."` |
| React 官方同页 | 承认手写的好处有限：`"There's quite a bit of boilerplate code involved when writing fetch calls in a way that doesn't suffer from bugs like race conditions."` |

> 一句话总结这一节：**「取消请求」是首选、但也有「忽略结果」这条官方并列的路；选哪条看成本 —— 而成本和边界都要能说出来。**

### 1.7 这一节的分水岭（把 §1.1–1.6 压成一句话）

> **「用量」是标准里有的东西**（OTel 定了属性名，供应商按固定字段上报，四家工具在做同一套 inclusive→exclusive 换算）；**「钱」是标准里没有的东西**（OTel 零 cost 属性，只肯说 token 是 "a proxy for cost approximation"），所以每一家都在自己那一层算 —— 网关、平台、应用，三处都有人站。
>
> **而「这一笔钱当时该按哪一版价算」是三家口径一致的那一条：不回填、只对新记录生效。**
>
> **「痕迹」那一半也有规范坐标**：CWE-598 管 URL 里的敏感数据、ASVS 14.2.2 / 14.3.2 管「演示开关」、OWASP 指引脱敏的时机 —— 而**唯一一条口径分岔的是「写之前脱敏」还是「收集后脱敏」**，本项目选前者。

对照本项目三句话：

| 层 | 我站在哪 |
|----|---------|
| **用量** | **完全落在标准里** —— 五个 token 属性四个能直接映射，三档桶互斥（inclusive→exclusive）也照做了 |
| **钱** | **站在应用层** —— 与 LiteLLM / Langfuse / LangSmith 同一类做法（网关 / 平台 / 应用三处都有人站），口径也一致（不回填） |
| **痕迹** | **写之前脱敏** —— 规范允许「收集后脱敏」，我选了更贵的那条（因为日志一旦落盘就擦不干净） |
| **对账 / 指标 / 关联 id** | **明确没有** —— 三条都记在欠账表里，不是漏项（见 §5.4 / §5.5 / §5.2） |

---

## 2. 本项目的逐条实现（带代码）

### 2.1 issue 27 · 工具轨迹落库：一个外键逼出来的架构决定

#### 2.1.1 起点：一张表没有生产写入方

`charagent_tool_calls` 表早就存在，但**从来没有代码写过它**。要给它装第一个生产写入方时，撞上了一个类型层面的障碍：

```python
# CharAgent/db/schema.py:384 (节选)
tool_calls = Table(
    "charagent_tool_calls",
    metadata,
    Column("run_id", String(128), ForeignKey("charagent_runs.run_id"), primary_key=True),
    Column(
        "message_id",
        String(128),
        ForeignKey("charagent_messages.message_id"),
        primary_key=True,
        comment="是哪条 assistant 消息发起的 (复合主键的三分之一, 且**不能为空**)",
    ),
    Column("tool_call_id", String(128), primary_key=True),
    ...
)
```

**三列复合主键**（这个设计本身值得讲）：

> 真实上游每轮都从 `call_0` 重新编号 —— 同一个 run 里会出现好几条「call_0」，单列主键第二次就撞；加上 `run_id` 只解决「跨 run」，同一个 run 的多轮之间仍会撞。**真正唯一的身份是「哪条 assistant 消息发起的这一次调用」**，所以 `(run_id, message_id, tool_call_id)` 三列才是完整的键。

问题：`message_id` 是**非空外键**，而当时的现实是：

- `messages` 行只在**运行收尾**才写；
- `message_id` 是**插入时才生成的随机 `uuid4().hex`**。

→ **运行期间要写一行工具调用，在类型上就做不到。**

#### 2.1.2 三条候选与选定

| 候选 | 怎么改 | 为什么不选 |
|---|---|---|
| a 只让 id 可推导，写入时机不变 | 派生 id | 运行中要写那条 assistant 行 → 库里出现半截行（提问行还没写、assistant 行先到）→ **审计视图里「提问」排在「我要调工具」之后**（`created_at` 错序） |
| b **messages 按轮增量写** | 改写入时机 | ✅ **选定** |
| c 只在挂起时写调用行 | 少写 | 不成立：挂起那条同样撞外键，且「挂起」与「普通」两套写法必然漂移 |

#### 2.1.3 派生主键：`(run_id, 下标)`

写时机改了之后，同一条消息会写**两次**（运行中那一拍 + 收尾补齐那一拍），而两次必须落在**同一行**上。修法是把 id 从「随机」换成「可推导」：

```python
# CharAgent/db/repositories/messages.py:42
def message_id_for(run_id: str, index: int) -> str:
    """由「哪次运行 + 第几条」派生的消息编号 (同一次运行里唯一的身份).

    为什么不用随机 id (2026-09-24, ticket 27): 记录层从「收尾一次性写」改成
    「产生即落库」之后, 同一条消息会写两次 (运行中那一拍 + 收尾补齐那一拍), 而
    `charagent_tool_calls.message_id` 是指向它的**外键** —— 两次必须落在同一行上.
    派生键是 `(run_id, 本次 run wire 历史里的下标)`: 运行内唯一 (历史只增不改),
    跨运行天然分开 (run_id 是 uuid4 hex), 而**同一次运行的第二段** (审批恢复)
    因此能直接算出第一段写下那一行的编号, 不必反查.
    """
    return f"{run_id}:{index}"
```

这一改**顺带给后续的 HITL 铺了路**：审批恢复时（同一次运行的第二段）能**直接寻址**第一段写下的那一行，不必反查。库里长这样：

```
run_id        = 'd425e954e51c463db54a9a25f06300e8'
message_id    = 'd425e954e51c463db54a9a25f06300e8:2'      ← 派生出来的
tool_call_id  = 'call_00_aFDQZe4yRAQVQDuGpuos3526'
tool_name     = 'list_my_orders'
status        = 'succeeded'
arguments     = '{}'                                       ← 原样 JSON, 不预解析
result_head   = '"{\"count\": 1, \"page\": 1, ...'
duration_ms   = 79
approved_by   = None      approved_at = None
```

#### 2.1.4 写入时机：四拍（这张表是全片的核心）

| 时机 | 写什么 | 谁触发 |
|---|---|---|
| **提问** | 提问行（可见） | `ChatSession.ask`，在 loop 之前 |
| 每轮 · **执行前** | 那条 assistant 隐藏行 + 本轮每条调用 `pending`（**挂起那条也先落 `pending`** —— 那是 issue 27 定的「结论不该是一行的第一个状态」，34 沿用） | loop · `_handle_tool_turn` |
| 每轮 · **执行后** | tool 回填行 + 每条调用终态（`succeeded` / `failed` + `result` + `duration_ms`；**挂起那条在这里被推进成 `needs_approval`**） | loop · 每轮收尾那一拍 |
| **收尾** | **补齐**（哪一轮没写上就补，幂等）· **修订**（截断续写时片段行改隐藏、拼合正文写进最后一条）· `runs.finish` | `ConversationRecorder` |

**四条纪律**：

1. `message_id` **可推导** → 同一行写两次是幂等的（`ON CONFLICT DO NOTHING`）
2. `runs` 仍是**两笔**（开始建行 + 收尾补余下）
3. 收尾的「补齐」是**安全网**：某一轮写失败只降级（记一笔、这一轮照跑）
4. **写的单位是「拍」，不是「消息」** —— 一拍 = 一次 `add_lines`（可含 1~N 行）+ 一次 `_write_calls`；**拍的条数与「轮」挂钩（每轮两拍），与消息条数无关**

> **第 4 条我自己写错过一次，这里留下改正后的版本**（2026-09-28）。原来写的是「不是『每条消息一写』，逐条写等于把 DB 往返塞进工具执行热路径」—— **在单工具的一轮里这句话是错的**：那一轮恰好只产生两条消息（assistant 行 + 1 条回填行），而它们分属两拍，所以「每轮写两次」与「每条消息写一次」**不可区分**。真实观测到的就是这么回事：
>
> | 时刻 | 落库动作 |
> |------|---------|
> | 刚提问 | `runs` 建行 + `messages` 写提问行 |
> | 模型决策要调工具 | `messages` 写一行 assistant 隐藏行（+ `calls` 幂等插入 `pending` 行） |
> | 工具执行完 | `messages` 写一行工具回填行（+ `calls` 推进终态） |
> | 一轮跑完 | `messages` 写答复行 + `runs` 回写 |
>
> **换句话说：每一次模型调用前后都有一次写 —— 这正是「产生即落库」的意思**，是刻意的代价（换来「被硬杀时库里有真实进度」），不是没优化掉的开销。
>
> **那「批量」体现在哪**（这才是第 4 条真正要说的事，而且只在**一轮多个工具调用**时看得出来）：
>
> | 事 | 逐条写会怎样 | 现在 |
> |----|------------|------|
> | N 条工具回填行 | N 次 `add_lines`（N 个事务） | **1 次** `add_lines`，一个 `execute` 带 N 组参数（`add_lines` 先造好整批 `rows` 再一次 `add_messages`） |
> | 写库顺序 | 变成**完成顺序**（`_execute_parallel` 是 `asyncio.gather`，谁先跑完谁先写 → 不确定） | **调用顺序** —— 与历史回填、事件三者对齐（代码注释：`"结果事件按调用顺序(非完成顺序)产出: 并发下事件序列确定, 且与历史回填顺序一致"`） |
>
> **而两拍的分界是语义线，不是性能线**：执行前那一拍**必须**早于工具执行 —— 它兑现的是「执行中被硬杀 / 被取消时，库里看得出它正要调什么」与「挂起那一刻 `needs_approval` 已经在库里」。**assistant 行与回填行之间隔着工具执行这件事是故意的，不是省下来的。**
>
> **两处没批量的要如实说**（免得把「一拍」读成「一次往返」）：
>
> 1. 调用行的**终态推进是逐条的**（`set_status` 每条一次 UPDATE）—— N 条调用的结果各不相同，没有可合并的写法；
> 2. 每拍的 `_write_calls` 都先走一次 `add_calls`，而第二拍那次**是空转**（幂等插入一条都插不进去，仍然是一次往返）。它是**为了「哪一拍没写上都不用记差了什么」**付的代价 —— 收尾那一趟能无脑重放，靠的就是这两笔都幂等。
>
> 所以「一拍」= 「一次消息批 + 一次调用批（可能空转）+ 每条调用一次推进」。

> **第 4 条修正版在面试上仍然好用，但说法要换**：**「我没有做成最细的粒度 —— 一轮里那几条回填行是一次批量提交的（顺序按调用序，不按完成序）。而每轮那两拍是为了语义：执行前那一拍必须早于工具执行，否则被硬杀时库里看不出它正要调什么。」** 前半句是性能，后半句是语义，**两条理由不能混着说**。

#### 2.1.5 代码：一个「事实」类型，两个消费方

Loop 不认识 `db/`（框架纪律）。于是把「一次工具调用留下的事实」做成 agent 侧的类型：

```python
# CharAgent/agent/utils/types.py:98
@dataclass(slots=True, frozen=True)
class ToolCallFact:
    """一次工具调用留下的事实 (loop 记, 落库协作者据此写 `charagent_tool_calls`).

    为什么由 loop 交出来而不是让记录层自己去 wire 历史里挖: 工具名与参数在历史里
    找得到, 但**结果与耗时**只有执行处知道 (`tool/executor.py` 的 `ToolExecution`),
    而它随那一轮结束就没了. 于是 loop 每轮把这几样打成一条事实交出去 —— 运行中那
    两拍与收尾的「补齐」读的是同一批事实 (单一来源, 两个消费方).
    """
    message_index: int          # 发起它的那条 assistant 消息在本次 run 里的下标
    tool_call_id: str
    tool_name: str
    arguments: str              # 模型填的原样 JSON 字符串 (不预解析)
    outcome: ToolCallOutcome = ToolCallOutcome.PENDING
    result: str | None = None
    duration_ms: int | None = None
    approval_prompt: str = ""
    approval_needs: tuple[str, ...] = ()
```

通道是一个**可选**协议（`runtime_checkable`，装配期用 `isinstance` 按形状认）：

```python
# CharAgent/agent/utils/types.py:218
@runtime_checkable
class TraceSink(Protocol):
    """落库协作者: 运行**进行中**把刚产生的东西交出去 (可选零件, ticket 27).

    与 `event_sink` (展示出口) 并列的第二个出口: 那个推给前端看, 这个落进记录表.

    **它是可选的**: 没给、或业务自己的记录员没实现这个方法, 就退回「收尾一次性写」,
    行为与从前逐字一样. 与 `RunRecorder` (db 侧协议) 的关系: 同一个对象
    (`db/recorder.py` 的 `ConversationRecorder`) 两侧都实现, 而 loop 只认本协议
    (agent 不 import db).
    """

    async def flush(
        self, *, thread_id: str, run_id: str, start: int,
        messages: Sequence[ModelMessage],
        calls: Sequence[ToolCallFact] = (),
    ) -> None: ...
```

装配（会话层，按结构匹配、不要求继承）：

```python
# CharAgent/client/session.py:231
# 落库协作者 (ticket 27): 记录员**顺带**实现了 `TraceSink` 才交给 loop ——
# 那一份是运行中途的增量落库, 而 record / record_unfinished 那三个方法是
# 人人都得有的. 按结构匹配 (不要求继承), 没实现就只有收尾那一拍
self._trace_sink: TraceSink | None = (
    recorder if isinstance(recorder, TraceSink) else None
)
```

#### 2.1.6 两拍怎么发：`_facts_of` 一个函数管两拍

```python
# CharAgent/agent/loop.py:226
def _facts_of(
    calls: Sequence[ModelToolCall],
    *,
    index: int,
    results: Sequence[ToolExecution | ApprovalRequest] | None = None,
) -> list[ToolCallFact]:
    """工具调用 (加它们的去向) → 事实列表 (记录层按它写 `charagent_tool_calls`).

    两拍共用它: `results` 为 None 是**执行前**那一拍 (状态是 PENDING, 还没有结果
    与耗时), 给了就是**执行后**那一拍 (结论与耗时都在手上了). 合成一个函数而不是
    写两遍: 两拍的字段必须逐一对应 (少一个字段, 库里那一行就少一列事实).
    """
```

**执行前那一拍**（工具还没跑，先落 `pending`）：

```python
# CharAgent/agent/loop.py:1046 (节选)
state.content_parts.clear()
state.history.append(assistant_wire(response))
# 发起这一批调用的那条 assistant 消息在历史里的下标: 工具回填消息还没入
# (它们在下面才 append), 所以此刻的末位就是它. 事实带上它, 记录层才算得出
# `message_id` (同一次运行的调度身份, 见 db/repositories/messages.py)
index = len(state.history) - 1

for call in response.tool_calls:
    await bus.emit(EventType.TOOL_CALL, **tool_call_data(call, turn=state.turn_count))

# 执行前那一拍 (ticket 27): assistant 隐藏行与「它要调这几条」先落库 ——
# 于是执行中被硬杀 / 被取消时, 库里看得出它正要调什么; 挂起 (#25) 那条
# 也是在这一拍由裁决改写成 needs_approval
await self._flush(
    state,
    start=index,
    messages=state.history[index:],
    calls=_facts_of(response.tool_calls, index=index),
)
# 这一步已经交出去的那条算交过了 (下面 _record_turn 只补交工具回填那几条):
# 「交过哪些」只有一个口径 (state.flushed), 两拍各数各的迟早会对不上
state.flushed = index + 1
```

**收尾那一拍**（在有工具轮的每轮末尾）：

```python
# CharAgent/agent/loop.py:1217 (节选)
await self._flush(
    state,
    start=state.flushed,
    messages=state.history[state.flushed :],
    calls=calls,
)
state.flushed = len(state.history)
```

**唯一的交付口**（判空只该有一处）：

```python
# CharAgent/agent/loop.py:1243
async def _flush(
    self, state: LoopState, *, start: int,
    messages: Sequence[ModelMessage],
    calls: Sequence[ToolCallFact] = (),
) -> None:
    """把刚产生的那几条消息与调用事实交给落库协作者 (没配 / 没开账 = 一步不走).

    为什么把 loop 的两拍收在同一个方法里: 「给谁、什么时候跳过」这两件事只该
    有一处判 —— 两拍各判一遍, 迟早有一处漏了 `run_id` 判空 (那样写出来的行没有
    归属).

    **不吞异常**: `TraceSink` 的契约是「实现方自己不抛」(写不进去自己降级),
    所以这里让它上抛 —— 一个会抛的落库协作者是本框架的 bug.
    """
    if self._trace_sink is None or state.run_id is None or self._thread_id is None:
        return
    await self._trace_sink.flush(
        thread_id=self._thread_id, run_id=state.run_id,
        start=start, messages=list(messages), calls=list(calls),
    )
```

#### 2.1.7 记录员那一侧：两笔写，都幂等

```python
# CharAgent/db/recorder.py:847 (节选)
async def _write_calls(self, *, run_id: str, calls: Sequence[ToolCallFact]) -> None:
    """写 / 推进这一批工具调用行: 先按 `pending` 建行, 再把有结论的推进到终态.

    两笔都幂等 (建行跳过已有的、推进把同一个结论再写一遍), 于是「哪一拍没写上」
    由收尾那一趟补齐, 不必记住差了什么.

    为什么每条都先建 `pending` 行 (而不是一笔写成结论): `needs_approval` (挂起等人)
    与 `succeeded` 都是**结论**, 而结论不该是这一行的第一个状态 —— 第一个状态是
    「模型刚发起」. 代价是每条多一次 UPDATE; 一个 run 的调用只有几条到几十条, 换来
    的是**挂起那条与普通那条走同一个形状** (两套写法必然漂移).
    """
    await self._calls.add_calls(run_id=run_id, calls=[
        build_tool_call(
            run_id=run_id,
            message_id=message_id_for(run_id, fact.message_index),  # 与运行中同一行
            tool_call_id=fact.tool_call_id,
            tool_name=fact.tool_name,
            arguments=fact.arguments,
            status=ToolCallStatus.PENDING,
        )
        for fact in calls
    ])
    for fact in calls:
        if fact.outcome is ToolCallOutcome.PENDING:
            continue                     # 执行前那一拍: 停在「模型刚发起」就是事实
        await self._calls.set_status(
            run_id, message_id_for(run_id, fact.message_index), fact.tool_call_id,
            tool_call_status_for_outcome(fact.outcome),
            result=fact.result, duration_ms=fact.duration_ms,
            approval_prompt=fact.approval_prompt or None,
            approval_needs=fact.approval_needs or None,
        )
```

幂等插入（Postgres 方言）：

```python
# CharAgent/db/repositories/tool_calls.py:148 (节选)
# **这个入口是幂等的** (ticket 27 起): 三列主键已经在了就跳过
# (`ON CONFLICT DO NOTHING`). 记录层要「执行前落 pending + 执行后回填终态」,
# 而收尾那一趟是**补齐**(哪一次没写上就补上) —— 于是同一批调用会被交给它两次,
# 第二次必须什么都不做, 且**不改已存在那一行的结论** (推进终态走 `set_status`).
session.execute(
    pg_insert(tool_calls).on_conflict_do_nothing(),
    [self._params(call) for call in calls],
)
```

#### 2.1.8 实施期真踩到的三个坑（面试上最值钱的部分）

| # | 症状 | 根因 | 修法 |
|---|------|------|------|
| 1 | 用户看不到「这一轮没答完」那句说明 | 说明行与提问行一起排位置，拿到了 `run_id:since+1` —— 而那个编号可能已经被这一轮真产生的消息占了，幂等写把说明行**当成已写过跳过** | 只有**来自 wire 历史**的那几条用派生编号，补进来的内部件给随机编号（用例 `test_the_unfinished_notice_does_not_take_a_wire_index` 钉住） |
| 2 | 同一条消息被交了两拍 | 先写的 `_handle_tool_turn` 没推进 `state.flushed` | 执行前那一拍把 `state.flushed` 推到那条之后（「一条消息交一次」这条口径不许破） |
| 3 | 幂等用例是**假绿** | 假库不认 `ON CONFLICT DO NOTHING` —— 同一批写两次在假库里会出两行 | `FakeRecordSession.execute` 按方言子句跳过已有的主键 |

> 第 3 条特别值得讲：**「我的假库比真库宽松，所以用例绿了而行为是错的」** —— 这是测试替身最典型的失效方式。

#### 2.1.9 真机时序（边跑边查，证明「产生即落库」真的成立）

| 时刻 | 库里已经有什么 |
|---|---|
| 2.75 s | 提问行（模型还在思考） |
| 3.54 s | assistant 隐藏行 + 2 条 tool 行 + 2 条调用行 |
| 4.47 s | 最终答复行 |

> 这一条回答的是：**「进程被硬杀时你能查到什么？」** —— 答：能看到它正要调什么、以及调用是不是已经有结论。

**验证计数**：`pytest` **1071 passed**（原 1052 + 新 19，零失败）· `pytest -m pg_db` **54 passed** · 真机两次运行。

---

### 2.2 issue 28 · 成本口径 + `trace` 只读入口

#### 2.2.1 起点：数据全在，缺的是「有人算、有人报」

| 数据 | 落在哪 | 状态 |
|------|--------|------|
| 全 run 累计总量 | `charagent_runs.total_tokens` | 已写 |
| 五个**分量** | `input_tokens` / `output_tokens` / `reasoning_tokens` / `cache_hit_tokens` / `cache_miss_tokens` | 已写（ADR-0005 建列） |
| `prompt_version` | `charagent_runs.prompt_version` | 已写 |
| `total_cost` | `Numeric(14,6) NOT NULL default 0` | **从来没人写过非零值** |
| **单价 / 折算金额** | —— | **全仓零代码** |

#### 2.2.2 决定一被推翻（这是本阶段最有故事的一处）

**第一版（2026-09-24）定的**：金额在**查询侧**派生，`total_cost` 继续不写。理由当时很硬：

> 「单价是**部署事实**，不是运行事实」「分量是原始事实，钱是解释」「写死在行里就只剩一种答案，且事后无从分辨」。

**第二版（2026-09-25）用户推翻**：

> 「`total_cost` 应该在运行结束的时候就算出来啊，怎么可能想查的时候再算，万一之前是一个价，过段时间查，供应商价格变了，价格不就对不上了吗」

**新口径（ADR-0018）**：金额与算式在**收尾那一刻**算好写死；`total_cost` 改成**可空**（`NULL` = 那一刻没算出来，`0` = 真的花了 0 元）；峰谷六档按**运行的开始时刻**判。

**为什么推翻是对的**（面试要能讲这层）：

- **账单是按当时那一版价目表开的** —— 查询侧现算会让历史金额跟着今天的价变，与账单**永远对不上**；
- **峰谷价逼着把「哪一刻」固定下来**：判据是运行的开始时刻，而只有 run 行知道它；
- ADR-0005 自己就写着「不把价格写进运行路径，**留到 L3**」—— 这就是那次回看。

#### 2.2.3 六档价目表：为什么规则必须由部署侧给

```jsonc
// .env.example —— CHARAGENT_MODEL_PRICES (一行 JSON)
{
  "timezone": "Asia/Shanghai",
  "peak_windows": [["09:00", "12:00"], ["14:00", "18:00"]],   // 半开区间
  "models": {
    "deepseek-flash": {
      "peak":   {"cache_miss": 2, "cache_hit": 0.04, "output": 8},
      "valley": {"cache_miss": 1, "cache_hit": 0.02, "output": 4}
    }
  }
}
```

单位一律是**每百万 token**的金额 —— 与各家价目表同形，部署侧照抄即可。

> **为什么不内置一套默认价**：内置默认值 = 换一家供应商就**静默算错钱**，而错的金额看起来和真的一样。这是全仓那条纪律的同类：**报 0 比报不出来更糟，因为 0 看起来像个答案。**

#### 2.2.4 峰谷判定：一个真数据逼出来的依赖

```python
# CharAgent/db/cost.py:197 (节选)
def tier_at(self, moment: datetime | None) -> TierVerdict:
    """某一刻算峰还是谷 (工作日 + 落在峰窗内 = 峰, 其余是谷)."""
    if moment is None:
        return TierVerdict(tier=None, gap=CostGap.NO_MOMENT)
    zone = ZoneInfo(self.timezone)
    local = (moment.replace(tzinfo=zone) if moment.tzinfo is None
             else moment.astimezone(zone))
    day = local.date()
    workday = _is_workday(day)
    if isinstance(workday, CostGap):
        # 判不了: 日历里没有这一年 (数据只到 2004-2026, 每年要升级), 或依赖没装
        return TierVerdict(tier=None, gap=workday, date=day.isoformat())
    if not workday:
        # 周末与法定节假日整天空闲 —— 谷价 (调休上班的周末在上面那一步已经是
        # True 了, 所以这里不用再判一次)
        return TierVerdict(tier=VALLEY, date=day.isoformat())
    clock = local.time()
    for start, end in self.windows:
        if start <= clock < end:
            return TierVerdict(tier=PEAK, date=day.isoformat())
    return TierVerdict(tier=VALLEY, date=day.isoformat())
```

**为什么必须引 `chinesecalendar`（真数据）**：

| 日期 | 星期 | 事实 |
|------|------|------|
| 2026-09-25 | **周五** | 中秋假期，**休息日** |
| 2026-09-20 | **周日** | **调休上班** |

按「周一到周五」硬判，**这两天都会算错**，而错的金额看起来很正常。

**判不了怎么办**：区分两种原因（日历过期 / 依赖没装），**留空 + 写明原因**，绝不按周末硬猜。启动自检会先拦住（`ensure_pricing_ready` → 进程起不来）。

#### 2.2.5 折算函数：一个刻意「不收」某些参数的签名

```python
# CharAgent/db/cost.py:520
def cost_of(
    *,
    model: str | None,
    table: PriceTable,
    moment: datetime | None,          # 本次运行的**开始时刻** (runs.created_at)
    input_tokens: int | None,
    cache_miss_tokens: int | None,
    cache_hit_tokens: int | None,
    output_tokens: int | None,
) -> RunCost:
    """一次运行的用量 + 开始时刻 + 模型名 -> 金额 (含「为什么算不出来」).

    **收 `input_tokens` 却不拿它定价**: 定价只看三档, 但输入总量是拆分的一份佐证
    —— 上游没报「未命中」那一档时 (OpenAI 系只给 `prompt_tokens_details.cached_tokens`,
    不给未命中数), 未命中 = 输入总量 - 命中. 两个数都是上游报的, 减出来的那一档
    因此仍是**事实**; 减出来是负数就地放弃 (那是上游数据不对).

    `reasoning_tokens` 与 `total_tokens` 刻意**没有参数位**: 前者是输出档的明细
    (计价会重复), 后者是三类不同价 token 的混合 (拿它乘等于把三档的价格关系抹平).
    """
```

**「推理不重复计价」这条是核实的，不是猜的**（三条证据）：

1. 上游把它放在 `usage.completion_tokens_details` 下 —— 这个层级在 schema 上就是 `completion_tokens` 的**分解**；
2. 实测样本 `tests/fixtures/llm/tool_path_thinking.json`：`completion_tokens = 59` 而其中 `reasoning_tokens = 16`；
3. 同一条样本里上游自己给的 `total_tokens` 恰好 `= prompt_tokens + completion_tokens`（459 = 400 + 59）—— 若推理另计一笔，上游自己的总数就对不上。

**落法很硬**：`cost_of` **连这个参数都不收**（传进去直接 `TypeError`），再加一条用例从展示层钉一遍。

> 面试金句：**「我把一个不该存在的参数从签名里删掉了 —— 这样它连被传进来的机会都没有。」**

金额一律走 `Decimal`，量化到 6 位（与 `Numeric(14,6)` 同标度），**JSON 里也存字符串**：

```python
# CharAgent/db/cost.py:315 (节选)
def to_detail(self) -> dict[str, Any]:
    """落库的形状 (**唯一一处定义**): 两列里那一列 JSONB 装的就是它.

    金额与单价一律存**字符串**: JSON 的数字是浮点, 而钱不能过浮点 (本仓那条
    「金额用 NUMERIC 不用浮点」在 JSON 这一层同样成立).
    """
```

#### 2.2.6 「算不出来」的十种原因（为什么不能合成一句）

`CostGap` 有十一个取值，因为**修法各不相同**：

```python
# CharAgent/db/cost.py:114 (节选)
class CostGap(StrEnum):
    """算不出金额的原因 (措辞在展示层, 这里只说是什么).

    分成这么细是因为**修法各不相同**: 没配价要去填配置, 缺分量要去查上游为什么
    没上报, 日历过期要去升级依赖 —— 合成一句「算不出来」等于把排查方向也吞了.
    """
    NO_PRICE = "no_price"                  # 这个模型不在价目表里
    NO_TOKENS = "no_tokens"                # 三档里至少有一档上游没上报
    NO_CALENDAR_DATA = "no_calendar_data"   # 日历里没有这一天所在的年份
    NO_CALENDAR_LIB = "no_calendar_lib"     # 配了峰谷价, 但没装日历依赖
    NO_MOMENT = "no_moment"                # 不知道这次运行从哪一刻开始
    UNFINISHED = "unfinished"              # 没跑完的运行
    BAD_CONFIG = "bad_config"              # 价目表压根没读成
    NO_DETAIL = "no_detail"                # 那一行没有金额也没有明细
    UNKNOWN_DETAIL = "unknown_detail"      # 库里那份明细读不懂
    TOO_SMALL = "too_small"                # 算出来的钱小于这一列的最小刻度 (1e-6)
```

`TOO_SMALL` 那一条值得单独讲：**三档各自先量化到 6 位再求和，比 1e-6 还小的花费会存成 `0.000000`** —— 而 0 在这一列的意思是「真的没花钱」，那是撒谎。修法是新增一个原因，**不写一个撒谎的 0**。

#### 2.2.7 金额写进去之后不被改（一个措辞与实现不符的实例）

代码评审抓出的真缺陷：`finish` 收到算得出来的金额时是**无条件写**的（守卫只加在明细上），而票据、`runs.py` 的 docstring 与 ADR 三处都写着「写进去之后不再改」。

**处置是改说法，不是改行为** —— 真正的规则是：

- **算得出来 → 按当前列重算**（覆盖）。五列是累计值，重算得到那一刻的总额；**累加会把同一笔算两次**；
- **算不出来 → 一个字节都不动**（不写 0、也不把已有金额清掉）；
- 状态推进碰不到那两列。

> 面试金句：**「这是我唯一一次『代码是对的、文档是错的』—— 我改了三处说法，并补了一条用例把『重算两次得到同一个数』钉住。」**

#### 2.2.8 第二个真缺陷：峰谷判定的时刻不该存在内存里

第一版把「开始时刻」记在记录员的 `_started_at` 内存里。后果：**换一个实例收尾**（挂起补做 / 对账补齐 / 换了进程）时判不了峰谷 → 峰谷部署下金额**静默留空**。

修法：**改成从运行行读 `created_at`**（库里本来就有这个事实）——「少一处状态，多一处对得上」。

#### 2.2.9 `trace` 只读入口

落点选独立模块而不是斜杠命令：

| 候选 | 为什么不是它 |
|------|------------|
| `client/utils/commands.py` 加一个 `Command` | 那是**交互式斜杠命令**（`RESUME` / `HISTORY` / `HELP` / `QUIT`）。`trace` 要的是「给一个 run_id，打印，退出」 |
| 塞进 `client/app.py` 的选项 | `app.py` 是交互式 REPL 的主体，再塞一个用完即退的模式会把两种生命周期搅在一起 |
| **独立模块 `CharAgent/client/trace.py`** | 与仓库既有惯例一致（`python -m CharAgent.client`），`pyproject.toml` 里本来就没有 `[project.scripts]`，不需要新增打包配置 |

用法：`python -m CharAgent.client.trace <run_id>`。

**打印布局里三个刻意选择**：

| 选择 | 为什么 |
|------|--------|
| 金额打 **6 位**小数（`¥0.001212`） | 与 `Numeric(14,6)` 同标度。4 位时一次小运行会显示 `¥0.0000` —— 正是本片要躲开的「看起来像个答案的 0」 |
| 算式**逐档写出来**（`cache_miss 416 x ¥1/M + ...`） | 「三档用量与折算金额」这条验收的正面兑现：只给合计的话，金额对不对只能**信框架** |
| 参数**原样打印**，不走 `render.format_arguments` | 那一个会解析并重新序列化，还会给畸形 JSON 加标注 —— 而 `arguments` 的列注释写着「畸形 JSON 正是自纠错路径的信号」，解析了反而丢证据 |

```python
# CharAgent/client/trace.py:233 (节选)
def _amount_block(run: Run) -> list[str]:
    """金额那一段: 库里那两列直接读出来 (算得出来给钱 + 算式, 算不出来给原因).

    算式那一行是这一片的说明书写在屏幕上: 读者自己就能验算, 不必信框架 —— 而算式
    是**当时**算出来存下的, 不是现在补算的 (补算会用今天的价, 那是另一笔钱).
    """
    cost = RunCost.from_detail(run.total_cost_detail)
    if run.total_cost is None:
        reason = _gap_text(cost, run.model)      # 明细里那句「为什么没有」就是答案
        return [f"  金额 (没有) {reason}"]
    head = f"  金额 ¥{_number(run.total_cost)}"
    if cost.tier is not None:
        head += f" ({cost.tier})"                # 峰价还是谷价, 事后要看的就是它
    return [head, f"    └ {_formula(cost)}"]
```

**读库对坏数据的三种态度**（老行不该让整段读不出来）：

```python
# CharAgent/db/cost.py:372 (节选)
if detail is None:
    return RunCost(gap=CostGap.NO_DETAIL)        # 那一趟压根没收尾 / 改口径之前的
if not isinstance(detail, Mapping):
    return RunCost(gap=CostGap.UNKNOWN_DETAIL)   # 形状不认识
```

**验证计数**：`pytest` **1203 passed**（第一版完工时 1133）· `pytest -m pg_db` **71 passed** · **CharApp 201 passed** · 真机两趟：`total_cost = 0.001189` / `0.001457`（第二趟 `output 230` 里含 `reasoning 147` —— **推理没有重复计价**），明细 `tier = valley`、三档逐项可验算。

#### 2.2.10 实施期留下两个坑（都踩过）

1. **迁移改名的代价**：`0003` 改名后，本机开发库的版本表里那条旧编号会让 alembic 直接报 `Can't locate revision`。处置：把版本表那一行指回 `0002` 再 `upgrade head`。**提交之后再改迁移名就没这么便宜了** —— 那正是「发布前才能压历史」那条 ADR 的边界。
2. **`chinesecalendar` 的 CI 只测到 Python 3.12**（本机 3.13 实跑过 ✓）；它每年 ~11 月发一版覆盖下一年，**过期后金额会集体留空**（启动自检会先拦住，报「升级依赖」）。

---

### 2.3 issue 29 · 日志脱敏：先堵三个实证泄漏点，再给通用规则

#### 2.3.1 规划期的自我修正

原计划写的是「框架给 `Redactor` 协议 + 通用规则」。**核实之后发现：已知的泄漏点三个里有三个都不在 `CharAgent/` 里** —— 框架侧 5 个模块共 11 处日志调用，**全是 warning/error，没有一处打过用户数据**。

于是顺序反过来：**先堵实证的洞，再给通用机制**。

> 面试金句：**「我改掉了自己的实施顺序 —— 原计划是先建通用机制。核实后发现框架侧一处都没打过用户数据，那就该先堵真的在漏的那三处，否则是『给一个没有病人的地方建医院』。」**

#### 2.3.2 三个实证泄漏点

| # | 泄漏 | 细节 |
|---|------|------|
| ① | **搜索词进访问日志** | `views_bff.py` 把搜索词作为 `?q=` 拼进上游 URL，而 CharApp 用 uvicorn `log_level="info"` 起、**access_log 未关** —— 访问日志会把**整条 URL** 记下来 |
| ② | **上游响应正文被截 200 字符写进日志** | `_detail` 把上游响应体截到 200 字符，而 `_DETAIL_LIMIT` 的注释直接写着「**只进日志**」—— 这段正文是**专门为了进日志才留下的**，里面有订单号、地址、余额 |
| ③ | **供应商错误体进日志** | 链：`build_service(writer=logger.info)` → `_retry_notice` → `f"{type(exc).__name__}: {exc}"` → `extract_error_message(response.text)` |

**① 的反差最能说明问题**：前端 `agent.html` **特意**把搜索词放进**请求体**，并注明理由「它完全可能是一个订单号」。**前端守住了，后端在最后一跳漏了。**

> **这条有 CWE 编号**：URL 里带敏感数据 = [CWE-598](https://cwe.mitre.org/data/definitions/598.html)，官方 Mitigation 就是「把它挪进 request body 或 header（可能要放弃 GET）」。项目的做法（前端走请求体 + 后端关 access log）正好是**治本 + 治标一起上**。规范原文见 §1.4.2。

**泄漏的确切机制（查过 uvicorn 源码）**：`--access-log/--no-access-log` 是 `is_flag=True, default=True`（**默认开启**）；默认格式（`uvicorn/config.py` 的 `DEFAULT_LOGGING`）是 `'%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'` —— 而 **`request_line` 里带着完整的 path + query string**。所以不是 uvicorn 多嘴，是**那一行本来就含 query**，而我们的 query 里有搜索词。

**①的修法为什么选「整个关掉」而不是打补丁**：

| # | 做法 | 代价 |
|---|------|------|
| **a** ✅ | **关掉 CharApp 的 uvicorn access log** | 砍掉一层观测。**但要如实说清**：access log 记的是「每条请求的 URL 与状态码」，而本项目的 URL **可能带用户数据** —— 关它是一次性的、覆盖**所有**将来带进 URL 的数据，而不是打补丁 |
| b | 搜索词改走请求体 | 要动两侧协议，而 URL 里**还可能**出现别的东西（会话编号、run_id）—— 治标 |
| c | 自定义 access log 过滤器只打 path 不打 query | 保住了观测，但 path 里的会话编号仍然带买家段；代价是要写 asgi 中间件或 uvicorn 的 log config |

```python
# CharApp/minimall/server.py:356 (节选)
def uvicorn_config(app: FastAPI, config: ServerConfig) -> uvicorn.Config:
    """这个服务怎么跑 (uvicorn 的三个设置, 单独一处以便用例钉住它们).

    - `access_log=False` (issue 29): 访问日志记的是整条 URL, 而本服务的 URL 里
      **会带用户数据** —— 列会话那条路的搜索词走查询串 (`?q=`), 而它完全可能是一个
      订单号. 关掉它不是「少记一点」, 而是一次性地盖住**所有**将来被带进 URL 的值;
      失去的那点观测由别处补 (启动 / 重试 / 降级那几行自己打, 外加 `trace` 只读入口).
    - `log_level="info"` 保留: 启动那一句人话与 uvicorn 自己的错误都靠它.
    """
    return uvicorn.Config(
        app, host=config.host, port=config.port, log_level="info", access_log=False
    )
```

**②的修法：只记状态码与错误码，正文一个字符都不进**：

```python
# app/minimall/views_bff.py:486
def _log_refusal(
    who: str, action: str, response: httpx.Response, *, level: int = logging.WARNING
) -> str:
    """上游拒绝时记一条日志: **状态码 + 错误码**, 把那个码返回给调用方 (issue 29).

    三条路 (流式转发 / `_call_upstream` 那六条 / 取消) 共用这一处, 因为「拒绝时记
    什么」是一条**纪律**, 不是三处巧合: 有状态码与错误码就够定位, 上游的响应**正文
    一个字都不记** (那是第三方返回的正文, 本层没有按字段脱敏它的知识). 收在一处
    之后, 想把正文加回来就得改这一个函数, 而不是在三条路里各塞一行.
    """
    code = _refusal_code(response)
    logger.log(
        level, "%s: 客服服务拒绝了这次%s: HTTP %d (%s)",
        who, action, response.status_code, code,
    )
    return code
```

> 「收在一处」这个形状值得注意：**它不是 DRY 洁癖，而是让「记什么」变成一条能被评审的纪律。**

**为什么不在这里做「按字段脱敏」**：那是**第三方返回的正文**，我们**没有它的字段知识** —— 不知道第 137 个字符是订单号还是商品名。在没有字段知识的地方硬做正则，正是「碰运气」。

#### 2.3.3 框架侧：`Redactor` 协议（两个方法分开，不是重复）

```python
# CharAgent/redact/protocol.py:30
class Redactor(Protocol):
    """把敏感值换成打码后的样子 (两个方法各管一类输入).

    形状上「有两个方法就算」, 不继承基类 (与框架里别的协议同款).
    """

    def redact_text(self, text: str) -> str:
        """自由文本打码 (**最后一道**: 规则型, 兜不住的如实留着)."""
        ...

    def redact_fields(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """按声明打码 (**主手段**: 知道它是什么, 就按它是什么打).

        Returns:
            dict[str, Any]: 打码后的**新**字典 —— 递进来的那一份不动 (它常常是还要
            落库 / 还要发给别人的原始数据).
        """
```

**分工判据（PRD §4.12）**：换成 code agent 还能用吗？

- **通用规则能**（手机号 / 邮箱 / 身份证 / 银行卡是**跨业务**的形状）→ 放框架；
- **「哪个字段敏感」只有业务知道** → 名单归业务。

#### 2.3.4 四条规则与一个中文的坑

```python
# CharAgent/redact/rules.py:41 (节选)
# 18 位身份证: 前 17 位数字 + 末位数字或 X (校验位)
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
# 16-19 位银行卡; 前后那一对顾盼见模块 docstring 第 2 条
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# 11 位手机号: 1 开头, 第二位 3-9 (虚拟运营商号段也在内)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 邮箱: 形状写宽松一点够用 —— 宁可不认 (漏一个), 不要误伤带 @ 的文案
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
```

**`(?<!\d)` 而不是 `\b` —— 这个坑值得单独讲**：

> 中文在 Python 的 `re` 里也算 `\w`，于是 `手机号13800000003` 里汉字与数字之间**没有**词边界 —— 用 `\b` 写的规则在中文日志里会**静默失效**，而中文日志恰恰是本项目的主场。有专门一条用例钉它。

**打码留「形状」不留「值」**：

| 规则 | 原文 | 打码后 |
|------|------|--------|
| `id_card` | `110101199003071234` | `1101**********1234` |
| `bank_card` | `6222021234567890123` | `6222***********0123` |
| `phone` | `13800000003` | `138****0003` |
| `email` | `buyer3@example.com` | `b*****@example.com` |

> 留头留尾**不是为了好看**：是为了让排查的人分得清两条日志说的是不是同一个值（`138****0003` 与 `138****0005` 是两个人）。**位数本来就是公开的格式信息**（手机号就是 11 位），按原长度补 `*` 不额外泄漏什么。

**顺序即语义**（先长后短）：18 位的身份证同时也是「16-19 位数字」，所以先认它；两者留头留尾都是 4 位，于是**就算认错，打出来的样子也一样** —— 规则之间不会互相打架（重叠的形状打出来的码相同，这是**有意挑的参数**）。

#### 2.3.5 路径打码：`**` 与构造期校验

```python
# CharAgent/redact/redactor.py:56 (节选)
class RuleRedactor:
    """通用规则 + 业务声明的字段路径 (「按路径打码」那一半).

    Args:
        fields: 字段路径 → 规则名 (或 `WIPE`), 形如
            `{"phone": "phone", "**.payment_password": WIPE}`.
    """
```

路径写法（**与 glob 同义，不自创第三种含义**）：

| 写法 | 意思 |
|------|------|
| `phone` | 顶层那个 `phone` |
| `customer.phone` | 再往里一层 |
| `addresses.*.phone` | 列表 / 字典里的每一个元素（一层） |
| `**.payment_password` | **任意层**里名字叫它的那一个 |

**三条行为约定**（都有用例钉着）：

1. **不改原件**：返回一棵**新**树，只有沿途该改的分支被拷贝；
2. **值不是字符串也打得住**：数字手机号 `13800000003` 按它的字符串形式打码；
3. **`WIPE` 不看形状**：支付密码这种「一个字符都不该留」的值走这条。

> **`**.名字` 这个写法值得单独讲一次**（它是本片唯一一处「我选了更贵的那条」）。同一份字段在不同接口里包的层数不一样（账户是一个平铺的 dict，地址是列表里的每一项），而**敏感不敏感跟包了几层没关系** —— 所以按名字认，不管在第几层。
> 代价是打码时要**递归**遍历结构。行业里有个现成的对照：**Sentry 的默认 scrubber 明确不递归**，原文给的代价是 `"for performance reasons"`，要用 `recursive=True` 才递归。我的取舍是**宁可递归**：这份数据的大小是可控的（一次日志的载荷），而"漏打"的后果是留痕里出现原文 —— 两害相权。**面试时可以主动说这个对照，并承认我付了性能代价换覆盖度。**

**构造期就报错**（因为**脱敏失效的表现不是报错，而是静默地漏**）：

```python
# CharAgent/redact/redactor.py:96 (节选)
if segments[-1] in _WILDCARDS:
    # 通配符是用来**找名字**的 (名字在第几层不确定), 而它自己指不出一格来 ——
    # 路径最后一段必须是那个字段名. 放过去的话 `{"a.**": WIPE}` 走到「整棵
    # 子树先被抹成一个字符串、再拿它当字典改」那条路上, 报一个看不懂的
    # TypeError, 而不是在装配那一刻说清声明写错了.
    raise RedactConfigError(
        f"字段路径的最后一段不能是通配符 (指不出是哪一格): {path!r}"
    )
```

> 这条是**代码评审实测复现**出来的真缺陷：`{"a.**": WIPE}` 构造期放行、打码时抛 `TypeError`。

#### 2.3.6 业务侧：名单 + 出口包一层

```python
# CharApp/minimall/log_redaction.py:44 (节选)
LOG_FIELDS: dict[str, str] = {
    "**.phone": "phone",
    "**.email": "email",
    "**.balance": WIPE,            # 余额没有「留哪几位」的说法, 留一位都是漏
    "**.receiver_name": WIPE,
    "**.detail": WIPE,
    # 支付密码: 今天**不在**任何载荷里 (ADR-0015 把它放进一次性载荷, 永不进 wire),
    # 这一条是提前声明 —— 哪天某个结构化日志真把它带进来, 打码立刻生效.
    "**.payment_password": WIPE,
}
```

```python
# CharApp/minimall/log_redaction.py:75
def redacting_writer(
    writer: Callable[[str], Any], redactor: Redactor
) -> Callable[[str], Any]:
    """把日志出口包一层: 每一行先打码, 再交给原来的出口.

    Note:
        只包**日志那条出口** —— 命令行那个入口的出口是开发者自己的终端, 不经过
        这里 (威胁模型是「谁能看到这些留痕」, 而终端不落盘).
    """
    def write(line: str) -> Any:
        return writer(redactor.redact_text(line))
    return write
```

**装配（一处改动，不碰框架）**：

```python
# CharApp/minimall/server.py:413 (节选)
def log_writer() -> Callable[[str], Any]:
    """**交给框架的那个**日志出口: 先打码, 再写进本进程的日志 (issue 29).

    说清它盖的范围: 它管的是「框架通过 `writer` 往日志里写的那几行」(现在就是重试
    提示), **不是**本进程所有日志 —— 框架自己打的异常栈不经过这里.

    为什么包在这一层 (而不是在框架那侧的重试提示里): 上游模型报错的正文会顺着
    「异常 → 重试提示 → writer」这条链一路走下来, 而 writer 是**装配处**递给
    框架的 —— 出口是日志就说明它会落盘, 落盘之后擦不干净, 所以只能在**写之前**
    打码.
    """
    return redacting_writer(logger.info, build_redactor())
```

#### 2.3.7 两条纪律（面试会被追问的点）

| 纪律 | 为什么 |
|------|--------|
| **不加「演示开关」** | ADR-0003 已否过一次：「一个『演示时把敏感数据打开』的环境变量是**安全反模式**」。本片同一条纪律 —— **名单是代码，不是配置**。这条有规范依据：OWASP ASVS **14.3.2**（生产必须关掉 debug 模式）与 **14.2.2**（所有「演示用」的配置必须移除），见 §1.4.3 |
| **不猜订单号** | 订单号的形状是本项目的**实现细节**，猜错了会**误伤真数据**（而误伤的表现是日志里该有的东西没了）。订单号不进日志靠**不打它**（业务那侧少记一处），不靠猜一条规则。**行业做法可对照 AWS**：自定义标识符（纯正则那档）被硬性限制为每条策略 ≤10 条、单条 ≤200 字符 —— 正则方案的表达能力本来就该被约束 |

#### 2.3.8 顺手改掉的第 4 处（评审提出）

`_with_user_copy` 那条「帧解析不了就原样转发」的分支，原来把**原始帧字节**截 200 字符打进日志。核实三点后改成记「哪条帧、多少字节、解析为什么失败」：

1. 那一帧**本来就会原样转发给浏览器**（同一买家看得见），所以记录它不等于新披露；
2. 但它的 message **不受任何固定表约束** —— 终端 error 帧里 `run_failed` 那条直接带异常文本；
3. 于是这一条改成**不记原文**。`_DETAIL_LIMIT` 这个常量随之**彻底消失** —— 本片之后，Django 侧再没有一处「把上游内容截一段进日志」。

> 这段最值钱的是**评审是怎么推翻我的**：我原先写的理由是「取固定表、不含上游正文」—— 那是把**解析成功**那条路的结论套到了**解析失败**这条路上（循环论证）。**面试时可以讲这个：同一段代码的两条分支，验证强度不一样。**

#### 2.3.9 真机那一趟

```
① GET /conversations?q=202609250008880000109999   → HTTP 200
   日志里该词 0 次、访问日志 0 行

③ POST /runs {"message": "我的余额还有多少"}
   → 日志: [retry] 第 1 次尝试失败, 0.5s 后重试: ... 手机号 138****0003 ...   ← 过了出口
   → 日志: 运行 ... 异常终止: ModelStatusError + traceback: ... 手机号 13800000003 ...
                                                                        ↑ **没**过出口
```

**同一趟里照出来一条真泄漏**（收口当天复盘才发现）：Django **开发服务器自带**的访问日志把**内部端点的完整路径**打出来，而订单号就在路径里：

```
[25/Sep/2026 13:29:45] "GET /api/minimall/agent/orders/202607290314520000107906/ HTTP/1.1" 200 1166
```

issue 29 关的是**客服服务自己**的访问日志（那里泄漏的是 `?q=`），Django 这一侧核的是**错误日志** —— **开发服务器自带的访问日志不在那一片的范围里**，也就是说这**不是「29 没做完」，是「没人问过这一侧」**。本片只记账、不修（收口片明令不改代码）。

> **这条同时是一个方法论教训**：第一次搜是搜在**追问之前**（那一刻日志里确实没有这个数），后来那趟追问把订单号带了进去 ——「0 次」这种断言要在**所有流量都跑完之后**再验一遍才算数。

**验证计数**：**CharAgent 1236 passed**（105 deselected）· **CharApp 207 passed** · Django `app.minimall.tests.test_bff` **103 passed** · `ruff check` + `format --check` 全过。

---

### 2.4 issue 30 · 两条搭车：BFF 共享 client · 历史响应竞态闸门

> 为什么搭 L3a 的车：两条都与 L3a 要动的**同一份前端与同一个 BFF** 重合 —— L3b 要往 `agent.html` 加确认卡、往 `views_bff.py` 加转发，**先把它修干净再往上加东西**。

#### 2.4.1 690 ms 花在哪儿（量出来的，不是猜的）

**现状**：`views_bff.py` 里 **3 处**创建 `httpx.Client`，全是「每次请求新建、作用域结束即关」，无连接池复用。而 `open_upstream` 的 docstring 明写着这是**有意的**：

> 「用每次请求一个 `httpx.Client`：Django 没有可靠的进程退出钩子」

**这条理由是成立过的**，不能装作没看见。所以修法不是「改成单例」，而是**在承认那条代价的前提下换一个更划算的**：

- **代价现在有数了**：本机 ~690 ms，而且它落在**首字之前** —— 用户先看到一次空转（issue 25 真机反馈「横幅先蹦一下」）；
- **那条理由的前提是「多 worker」**：项目自己已经写明部署是**单进程**（`CharAgent/server/sessions.py` 的「单进程」一节）；单进程下 `atexit` 是**可靠**的；
- 于是：**模块级共享一个 client + `atexit.register(client.close)`**，并把「依赖单进程」这个前提写在 docstring 里 —— 与 `sessions.py` 那条前提**同源**，不是新引入的假设。

> **为什么这里需要 `atexit` 这种替代品**（这条比「我用了个单例」值钱）：FastAPI / Starlette 有**官方的 `lifespan` 钩子**，「进程级 HTTP 客户端」就是它文档里的教科书用例（官方示例：`async with httpx.AsyncClient() as client: yield {"http_client": client}`，见 §1.5.5）。**而 Django 这一侧没有等价的钩子**（`runserver` 不发 lifespan 事件）—— 这正是原注释那句「Django 没有可靠的进程退出钩子」的来源。**同一件事，在一个框架里有官方位置，在另一个框架里要自己找替代品。**

**钱具体花在哪儿**：

> `httpx.Client.__init__` 会为「主传输 + 两个代理传输」各建一个 SSL 上下文，而 `ssl.create_default_context()` 里的 `load_verify_locations`（读系统证书库）本机一次约 **310 ms**，三次就是 ~930 ms。而 `CHARAPP_SERVER_URL` 是 `http://127.0.0.1:1007`，**那些上下文一次都用不上**。

**这一改有官方背书**（原文见 §1.5.1）：httpx 官方明写「只要不是试验/一次性脚本/原型，就该用 `Client` 实例」，并且把「**a single global client instance**」列为正当写法之一。而改前的写法（每次请求 `with httpx.Client(...)`）正落在官方说的 "prototypes" 那一档。

**一条要主动交代的边界**：`atexit` **不在 httpx 的官方文档口径里**（对发布包 0.28.1 全量源码 grep `atexit` 也是 0 命中）。官方给的是**上下文管理器**或**显式 `.close()`**；httpcore 文档给的兜底是「池在 GC 或解释器退出时自动关闭」。我用 `atexit` 是想让「关」**确定地发生在一个已知时刻**，而不是等 GC —— 这是工程选择，不是官方推荐，记在 §1.5.2。

**连接额度也是刻意收紧的**：官方默认是 `max_connections=100` / `max_keepalive_connections=20` / `keepalive_expiry=5`，本项目设成 **10 / 10 / 5** —— 因为并发上界本来就小（页面同一时刻只跑一个运行），而额度真被占满时会**阻塞等 `pool` 超时**（httpcore 原文：`"Any attempt to send a request on a pool that would exceed this amount will block until a connection is available."`），所以把上限压到自己能解释的量级。

```python
# app/minimall/views_bff.py:233 (节选)
# 与助手服务之间的连接: **整个进程共用一个 `httpx.Client`**, 进程退出时关掉.
#
# 上一版是每次请求新建一个, 当时的理由写在 `open_upstream` 的 docstring 里. 那条
# 理由对**多 worker** 部署成立, 而本项目的部署是**单进程**. 单进程下 `atexit` 是
# 可靠的, 于是那笔代价换成了它的收益: 本机实测每条路都要 690~970 ms 一笔, 而且这笔
# 钱花在**首字之前**.
#
# 复用的代价: 池子里那条闲置连接可能已经死了 —— **httpx 自己认得出这种情况**:
# httpcore 在决定一条闲置连接还能不能用时, 除了 keepalive 到点, 还会看那个 socket
# 是不是已经"可读" (对面关了连接才有这个状态, `http11.py` 的 `server_disconnected`),
# 认出来就换一条新连接.
#
# 唯一漏得过去的是**请求恰好落在上游咽气的那一瞬**: 那一次抛 `httpx.ReadError`,
# 用户看到一次「客服暂时联系不上」, 再点一次就好. 这里**刻意不做重发**.
_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=10)

# 超时**不设在客户端上**: 三条路的耐心各不相同 (问答 120 秒 / 其余 10 秒), 共用一个
# 客户端之后, 每个调用点各自把 `timeout=` 传进去. 客户端上那份默认值一旦被谁当成
# 兜底依赖上, 「取消那条路只等十秒」这条就悄悄没了.
_CLIENT = httpx.Client(limits=_LIMITS)
atexit.register(_CLIENT.close)
```

**实测数字**（上游换成一个只回 200 的本机服务，跑的是**真的** `views_bff` 转发函数）：

| 路径 | 改前 中位数 / 最小 | 改后 中位数 / 最小 |
|------|------------------|------------------|
| 列会话（带搜索词） | 815.7 / **690.1** ms | 15.4 / 2.6 ms |
| 读历史 | 822.4 / 810.7 ms | 2.4 / 2.0 ms |
| 改标题 | 840.5 / 811.7 ms | 8.7 / 3.0 ms |
| 置顶 | 837.0 / 815.0 ms | 9.5 / 2.8 ms |
| 取消 | 973.6 / 895.9 ms | 15.8 / 2.8 ms |
| 问答首字（开流+收干） | 961.2 / 936.2 ms | 9.1 / 2.9 ms |

#### 2.4.2 一次「写完又撤掉」的优化（面试上很加分）

**原始怀疑**：助手服务重启后，池子里那条闲置连接已经死了，复用它发出去的那一次会失败一次。

**实测把这个说法拆成了两半**：

| 情形 | 实测结果 |
|------|---------|
| 上游被杀 → **隔一会儿**（0.2 秒就够）再发 | **成功**，而且上游新实例收到的是**一条新连接** |
| 上游被杀 → **同一瞬**发请求（对面关连接的信儿还没到本机） | 抛 `httpx.ReadError`（WinError 10054），那一次失败；再发一次就好 |

**第二行才是漏网的** —— 但那一瞬上游正不在，**重发换来的还是「联系不上」**，用户能看见的结果一模一样。

**所以没做重发**（一度写完又撤了）：多一层代码、多一个「到底发了几次」的疑问，换不来收益。按票据那句「**别为了兑现规划去改一个不划算的东西**」撤掉，只把这段实测写进代码注释。

**事后查到两条官方依据，都支持这个判断**（原文与出处见 §1.5.6）：

1. 我观测到的机制**确实在源码里**：`httpcore/_sync/http11.py` 的 `has_expired()` 会看 `get_extra_info("is_readable")`，注释原文就是「闲置连接如果可读，唯一合法的状态是对端即将断开」；探测用零超时的 `select` / `poll`（`is_socket_readable` 的注释：`"we don't want to block forever if it's not readable"`）。**这个问题历史上被反复修过** —— httpcore 0.14.2 `"Failed connections no longer remain in the pool."`、0.14.3 `"Fix race condition when removing closed connections from the pool."`；
2. **httpx 自己的 `retries` 参数覆盖不了那一瞬** —— `HTTPTransport(retries=N)` 的官方语义是 `"The maximum number of retries when trying to **establish a connection**."`，即**只覆盖建连阶段**；httpx 文档也没有承诺对 `server disconnected` 自动重试。

> **面试金句**：「我想加一次重发，先查了两件事：httpcore 自己就会认死连接，而 httpx 的 `retries` 语义是**建连时的重试**、覆盖不了我遇到的那一瞬。**所以我不指望官方重试、也没自己加 —— 量过之后判定不划算。**」

**连接额度那条问号也拿真实 socket 量过**：先把一条上游流**开着不读**（连接被占住，正是票据担心的那种），再发三条短请求：5.1 / 23.5 / 4.7 ms，没有被饿死。

#### 2.4.3 前端竞态：闸门样板在前端，不在 BFF

| 函数 | 防竞态方式 |
|------|-----------|
| `loadConversations` | ✅ **递增 ticket + 回来比对**：`const ticket = ++listRequests`，回来时 `if (ticket !== listRequests) return` 丢弃旧响应 |
| `loadHistory` | ⚠️ 只有一个布尔 `finished` |

**差别**：ticket 能区分「两次加载谁更新」；布尔只能区分「聊没聊过」。于是**两次并发 `loadHistory` 之间无法判先后** —— 快速切会话时，先发的慢响应仍可能盖掉后发的快响应，**看到的是上一个会话的历史**。

**修法现成**：照 `listRequests` 再来一道 `historyRequests`。

> **这个修法正好命中官方并列的两条路之一**：React 官方文档对「Effect 里 fetch」的原话是 —— **`"the cleanup function should either abort the fetch or ignore its result"`**，并且给这个 bug 定了名（`"Bugs like this are called race conditions because two asynchronous operations are 'racing' with each other"`）。前端这道 ticket 闸门属于**「忽略结果」**那一支（原文见 §1.6.2）。
>
> **面试时可以直接答这个取舍**：「abort 与 ignore 两条都是官方口径，我选 ignore —— 因为保留旧响应的成本极低（一次 JSON），而 abort 要多一条错误分支（还要处理 `AbortError` **不算错误**这件事，Chrome 官方博客专门说了这个坑）。」

```javascript
// templates/minimall/agent.html:795
// 谁发的那一次读历史 (下面那个闸门用). 拉历史的地方有三个 (首屏 / 重试按钮 / 切会话),
// 而这三个**可以叠在一起**: 切会话能连着切. 叠起来的表现是**先发的那次后回来**, 于是
// 上一个会话的历史盖到当前这一段上 —— 用户看到的是另一段对话的内容.
let historyRequests = 0;

async function loadHistory(mayHaveHistory) {
  if (!finished) return;   // 用户已经在问了, 别把历史盖到新一轮上
  const ticket = ++historyRequests;
  historyLoading = true;
  syncComposer();
  try {
    await loadHistoryBody(ticket, mayHaveHistory);
  } finally {
    // 收工 (成功 / 失败 / 被丢掉) 都要走到这儿: 锁是**拿在这一次手里**的, 少放一次
    // 输入区就永远锁着. 只有最新的那一次能真放 —— 连切会话时旧那次回来得早, 它一放
    // 用户就能在还没画完的时候提问了.
    if (ticket === historyRequests) {
      historyLoading = false;
      syncComposer();
      if (document.activeElement === document.body) input.focus();
    }
  }
}
```

```javascript
// templates/minimall/agent.html:859 (节选, 渲染前那道闸门)
if (!finished) return;                      // 防「话正在说」
if (ticket !== historyRequests) return;     // 防「位置过期了」—— 两道守卫是两回事
```

**真机验证（浏览器 + 受控延迟，双向都跑过）**：三个会话 A/B/C，A 最慢（6 秒）最后落地、C 最快（2 秒）最先落地，快速连点 A→B→C。

| | 聊天区最终内容 | 判读 |
|---|---|---|
| **修复前**（模板回退到 HEAD） | `会话C…会话B…会话A…` 三段叠成一屏 | 缺陷复现 |
| **修复后** | `会话C…` 只有一段 | 旧响应被丢弃 |

#### 2.4.4 「加载期间不让问」：一个布尔背后的设计

第二轮补的那条（用户要求）：**用户在等历史时提问**、而那次提问快过历史加载时，回来的历史会追加到那一轮下面。补的办法是**加载期间不让问**。

```javascript
// templates/minimall/agent.html:302
// 输入区能不能用: 没有正在跑的问答, 没有正在等的历史, 也没有未决的挂起.
// **只留这一个出口** (而不是在 `ask` / `end` / `loadHistory` / `setChatPlaceholder`
// 里各写一遍 `input.disabled = ...`): 这几件事各自会变, 各写一遍就是"谁后写谁赢",
// 迟早对不上.
function syncComposer() {
  // 第三个条件 `pendingApproval` 是 L3b（issue 36 的确认卡）加的 —— 写在这里
  // 是为了看清「唯一出口」这条纪律的回报: 加一个新锁源只动了这一个函数.
  const locked = !finished || historyLoading || pendingApproval !== null;
  input.disabled = locked;
  send.disabled = locked;
  document.querySelectorAll('.agent-example').forEach(function (button) {
    button.disabled = locked;     // 示例按钮也是"提问"的另一个入口
  });
}
```

> **这一处最值得讲的是「唯一出口」这条纪律**：三个锁源（跑着 / 加载中 / 挂起）各自会变，**各写一遍就是「谁后写谁赢」**。后来的 L3b 确认卡正好是第三个锁源 —— 加进来只改了这一个函数。

**真机抓到的一个边界**：横幅被摘下来的那段时间锁上、挂回来时那三个按钮是死的（「答完一轮再点新对话，按钮点不动」）→ `setChatPlaceholder` 里也调一次 `syncComposer()`。

> 同样值得讲：**验模板改动必须重启 Django**（跑久的 `runserver` 会继续发旧模板 —— 这次就踩了一次，白跑两轮）。

**验证计数**：`test_bff` **103 → 109**（新增 `BffSharedClientTest` 3 条 + `AgentPageTest` 3 条）。

---

### 2.5 issue 31 · 收口：真机验收 + 欠账清点

#### 2.5.1 收口片做什么（这条方法论本身可讲）

> **收尾片不是「写总结」，它的主要价值是清点与交接。** —— 「欠账不清点，下一片就会把它当成漏项重新发现一遍。」

四件事：

| # | 做什么 | 关键纪律 |
|---|--------|---------|
| 1 | 真机验收（PLAN 定的两条逐条走） | 数字与输出**贴进票据** |
| 2 | 如实记一条「本阶段没有新增插件」 | 写进 PLAN 的「核实后的修正」块 |
| 3 | 文档同步 | `git diff` 能看出改了哪几处 |
| 4 | **欠账清点**（每条都有归属，没有「待定」） | 归属只有五种：`L3b` / `L4` / `#38` / `单开一片` / `不做` |

> **「别在收口片里顺手做优化」**：发现的问题记进欠账表，不要当场改 —— 那会让「L3a 到底做完了没有」变模糊。本片第四节里第 8（重发）、第 9（代理）、第 10（Django 访问日志带订单号）三条都只是记账，**一行代码没动**。

#### 2.5.2 一份「欠账表」长什么样（这是最像工程的一页）

往下传的（有明确接手下家）：

| # | 欠账（一句） | 为什么当时没做 | 归属 |
|---|-------------|--------------|------|
| 1 | 框架自己打的**异常栈**不过业务递给它的日志出口 | **没发现** —— 29 号真机跑出来的 | **#38** |
| 2 | 框架**没有统一的日志出口**：五个模块各自 `logging.getLogger("charagent.<子包>")` | **取舍** —— 与脱敏无关，混进去会让 diff 说不清 | **#38** |
| 3 | `redact_fields`（按字段名打码）那半**生产零调用**，约 80 行 | **取舍** —— 交付物点名要它（为结构化日志预支） | **#38** |
| 4 | 工具执行中被取消会留一行**永不推进的 `pending`** | **取舍** ——「比编一个 `failed` 诚实」 | **L3b** |
| 5 | `resume()` 补做的调用**不属本次 run** 时被外键拦下（降级一条 warning） | **取舍** —— 两条路可选，写进 docstring | **L3b** |
| 6 | 问答路遇到「请求恰好落在上游咽气那一瞬」会失败一次 | **没发现** → 查清后**决定不做重发** | **L3b**（要覆盖它得上幂等键） |
| 7 | BFF → 客服服务的流量**绕一跳系统代理**（`trust_env=True` 默认读 `getproxies()`） | **没发现** —— 查 690 ms 构成时撞见的 | **单开一片** |
| 8 | **Django 开发服务器的访问日志把内部端点完整路径写进日志**，订单号就在路径里 | **没发现** —— 没人问过这一侧 | **单开一片** |

刻意不做的（**不是漏项，写下来免得下一次重新发现**）：

| # | 是什么 | 为什么不做 |
|---|--------|-----------|
| 9 | `views_bff.py` 打**被拒的**会话编号用 `%r` | 打的是**被拒的那个值**，且编号本身不是个人数据 |
| 10 | `trace` 的 `0ms` 与「没计时」在屏幕上长得一样 | 真出现「看着像 0 其实是没记」再改 |
| 11 | `¥` 符号写死、价目表可配任意币种 | 等真有第二家不同币种的供应商再说 |
| 12 | 若干「同形代码」（DRY 阈值是重复 ≥ 3 次才抽） | 这些是 2 处，或「抽了会更绕」 |
| 13 | 库里存着的原文（数据合规的删除权） | ADR-0016 明确接受的代价；触发条件是「对外提供删除入口」那天 |

> **这张表就是面试里「你哪里没做好」的标准答案**：不是「我没做」，而是「**我知道它没做、为什么没做、什么时候该回头做**」。

#### 2.5.3 一个「定案但不实现」的例子

`trace` 的**发现路径**：框架 CLI 从来不打印 `run_id`，跑完一次运行手上没有那个编号（28 号留下的边界）。

**本片定案** → 选「让框架 CLI 在完成行里打一次 `run_id`」，**不选**「`trace` 加一个看最近一次」（后者要给只读入口引入「哪个算最近」的隐式状态，多租户下说不清）。

**本片不实现**：改法要动 `ChatSession` 的对外面 —— `LoopResult` 是**循环层**的结果，压根不认识 `run_id` 这个记录层的概念，所以要么给会话加一个「上一次的编号」，要么把它并进返回值。**不是一行的事**，不塞进收口片。

---

## 3. 对照表：企业用什么 vs 我做了什么

| 能力 | 企业主流做法 | 本项目（L3a） | 差在哪 / 为什么 |
|------|------------|--------------|----------------|
| 链路追踪 | OpenTelemetry SDK + 自动埋点，span 上报到 Jaeger / Tempo，按 trace_id 串起跨服务调用 | 自己写 `charagent_tool_calls` 表 + `python -m CharAgent.client.trace <run_id>` | **没有 trace_id 跨进程传播，没有自动埋点，没有后端存储**。换来的是「零依赖、给个编号就能回看」—— 项目的诉求就是这个（DESIGN #41 明写） |
| span 层级 | `run → turn → tool_call`（本项目）对得上 OTel 的 agent / LLM / tool span 层级 | 表里 `(run_id, message_id, tool_call_id)` 三层主键**天然就是这个层级** | 「层级」这件事我是用**主键**表达的，企业用 **parent_span_id 表达** —— 同一件事的两种表示 |
| 成本归因 | 网关层（LiteLLM / Helicone）或可观测平台统一记录，价目表在平台侧配置 | 价目表是环境变量一行 JSON，折算在**收尾那一刻**由记录员算好写死 | 企业一般在**网关**做（一次接入全公司受益）；本项目在**应用层**做（因为只有一条链路，且金额要与账单口径绑定） |
| 缓存计价 | 平台按 input / output / cached / reasoning 分量分别计价 | 三档（`cache_miss` / `cache_hit` / `output`）+ 峰谷两套，`reasoning` **明确不重复计** | 分量划分与企业一致；**峰谷价 + 中国节假日日历**是我这里独有的复杂度（供应商这么定价，就得这么算） |
| 日志脱敏 | 三种落点：**SDK 端**（Sentry `EventScrubber`：按**字段名**比对 denylist，默认不递归）· **Agent 端**（Datadog `mask_sequences` 正则规则）· **摄入时**（AWS CloudWatch data protection policy：在所有出口遮蔽，只有带 `logs:Unmask` 的人能看原文）· 库（Presidio 两段式：Analyzer → Anonymizer） | `Redactor` 协议（框架给规则、业务给名单）+ 装配处包一层 writer，**写之前**脱敏 | **框架侧没有统一日志出口**（五个模块各自拿 logger），所以「框架自己打的异常栈」绕过出口 —— 已记为欠账。企业做法通常是**统一出口**（一个 logger 或一个 processor 链），这一点是我明确的差距。**另外**：structlog 官方文档里**检索不到**现成的脱敏 processor 章节（这是个检索负结果，如实记着） |
| 访问日志 | 一般默认开着，靠「URL 里不放敏感数据」这条设计纪律（CWE-598 的 Mitigation 原话） | **整个关掉** + 搜索词走请求体 | 企业更常见的做法是**不把敏感数据放进 URL**（治本），我这里是治本 + 治标一起上；关掉 access log 的代价我如实记了（少一层观测） |
| 结构化日志 + 关联 id | structlog processor 链 + `merge_contextvars`（脱敏 processor 必须在 renderer **之前**）；或标准库 `Filter` 注入；OTel 注入 `otelTraceID` / `otelSpanID` | **没做**（`DESIGN.md` #38 整片空着） | 这是 L3a 里我**最大的明确缺口**：五个模块各自拿 logger、没有统一出口、没有 request_id 贯穿。已归属 #38，触发条件是「结构化日志那一片开工」 |
| HTTP 客户端 | **lifespan 钩子里建一个进程级 client**（Starlette/FastAPI 的官方教科书用例：`async with httpx.AsyncClient() as client: yield {"http_client": client}`）；连接池 + 重试 + 熔断 | 进程级 `_CLIENT` + **`atexit` 关闭** + 超时逐次传 + **明确不做重发**；额度收紧到 10/10 | 官方给的是**上下文管理器 / 显式 close**，**`atexit` 不在 httpx 文档口径里**（0 命中）—— Django 没有 lifespan 钩子，所以只能找替代品。没有熔断、没有退避重试：上游是**本机回环**，失败面只有「那一瞬」，量过之后判定不划算。**这条要主动说，别装作是官方推荐** |
| 前端竞态 | `AbortController` 取消旧请求；或**序号闸门 / 忽略结果** | 递增 ticket 闸门 + `finally` 里释放锁 | **React 官方把两条路并列写出来了**（`"either abort the fetch or ignore its result"`）—— 我选「忽略结果」，因为保留旧响应成本极低（一次 JSON），而 abort 要多一条错误分支（还要处理 `AbortError` 不算错误） |
| 可观测平台 | Langfuse / LangSmith / Phoenix 等一体化平台（trace + 评估 + prompt 版本） | **不接**（DESIGN #41 明写「没接 OpenTelemetry / Langfuse 那一类」） | 这是**刻意的**：项目的定位是从 0 手写框架。接一个平台等于把「可观测」这块抽象掉 —— 而这块本身就是要展示的能力 |

**参考实现的那三行，逐条更细地对一次**：

| 项 | 企业 | 本项目 | 一句话差距 |
|----|------|--------|-----------|
| 工具 span 的属性名 | `gen_ai.tool.name`（Required）/ `.call.id` / `.call.arguments` / `.call.result` | `tool_name` / `tool_call_id` / `arguments` / `result` | **四个全对上**，只差 OTel 那层 trace_id / span_id 外衣 |
| token 属性名 | `gen_ai.usage.{input,output,cache_read.input,reasoning.output}_tokens`（+ `cache_write`） | 五列：`input` / `output` / `cache_hit` / `cache_miss` / `reasoning` | **四个能直接映射；`cache_write` 我没有**（DeepSeek 不区分这一档） |
| 桶的口径 | 必须**互斥**（Langfuse 原文：inclusive 要换算成 exclusive） | `cache_miss = input - cache_hit`（`_fill_missing_tier`） | **同一个换算**，我的注释与它的文档说的是同一件事 |
| 金额 | **标准里没有**；LiteLLM / Langfuse / LangSmith 各自在网关 / 平台层算 | 应用层：收尾那一刻算好写进两列 | **位置不同，口径一致**（都不回填） |
| 对账 | LiteLLM 的 `spend_capture_rate`（captured / provider，每日 job，阈值 0.9，指标 + 告警） | **没有** | 我验的是「可复算」，不是「与账单一致」（issue 31 明写） |
| 指标 | `gen_ai.execute_tool.duration` / `gen_ai.invoke_agent.tool_calls` / p95 面板 | **没有** | `DESIGN.md` #39 未做，三支柱缺一根 |
| 日志关联 id | structlog `merge_contextvars` / OTel 注入 `otelTraceID` | **没有**（五个模块各拿 logger） | `DESIGN.md` #38 未做，是我最大的明确缺口 |
| 存储 | ClickHouse / 对象存储 + Parquet / ES | PostgreSQL 两张表 | 规模不同，够用；**这条要主动认** |

> **这张表的用法**：面试官问「你这个和企业里的差距在哪」，**别泛泛说「规模小」** —— 按这张表从上往下数，**每条都能说清「是同一个东西 / 是位置不同 / 是我明确缺的」**。这就是「知道自己站在哪」的样子。

---

## 4. 面试主菜：五处能讲深的设计

### 4.1 「产生即落库」：一个外键逼出来的架构决定

**怎么讲（时间线版）**：

1. 我要给 `charagent_tool_calls` 装第一个写入方；
2. 那张表的 `message_id` 是**非空外键**指向 `charagent_messages`；
3. 而消息行**只在运行收尾才写**，`message_id` 还是**插入时才生成的随机值**；
4. → **运行期间写一行调用，在类型上就做不到**。不是「难写」，是**做不了**；
5. 于是我有三条路：让 id 可推导 / 把 messages 改成按轮增量写 / 只在挂起时写调用行；
6. 选**第二条**：id 派生 + 写时机改掉。

**为什么第一条不够**（这是面试官会追问的）：只改 id 不改时机，运行中就会往库里写**半截行**（提问行还没写、assistant 行先到）→ 审计视图里「提问」会排在「我要调工具」**之后**（`created_at` 错序）。

**顺带修掉的三件事**：

1. `created_at` 变成**真的产生时刻**（今天是收尾那一刻 + 微秒偏移）；
2. 运行中刷新页面能看到自己刚问的那句；**进程硬杀时库里有真实进度**；
3. **同一次 run 的第二段**（审批恢复）能**直接寻址**第一段写下的那一行。

**面试官可能问**：

- *「为什么不是每条消息写一次？」* → **先纠正问题的前提**：单工具的一轮里**就是**每条消息一次写（那一轮只有两条消息、分属两拍，两者不可区分）。**批量的收益要在「一轮多个工具调用」时才看得出来** —— 那时 N 条回填行是**一次** `add_lines`（一个 `execute` 带 N 组参数），而不是 N 次事务；顺带让写库顺序等于**调用顺序**而不是完成顺序（`gather` 并发下完成序是不确定的）。
- *「那为什么不一拍写完，非要分两拍？」* → **那是语义不是性能**：执行前那一拍**必须**早于工具执行 —— 否则被硬杀时库里看不出它正要调什么，挂起那一刻 `needs_approval` 也来不及落库。**两条理由要分开说，别把语义说成优化。**
- *「写失败了怎么办？」* → 运行中那一拍**只降级**（记日志、这一轮照跑），收尾那一趟**补齐**（幂等）；只有收尾也失败才落进 `_missed` 机制、下一次成功写入时补一条用户可见的提示行。
- *「`pending` 会一直留着吗？」* → 普通调用那行 `pending` 只存在**几十毫秒**（工具执行那一下）；挂起那条的 `needs_approval` 才会一直等。**认下的一条边界**：工具执行中被取消会留一行永不推进的 `pending` —— 「比编一个 `failed` 诚实」。

### 4.2 金额为什么写死：我推翻了自己前一天的决定

**怎么讲**：

- **第一版**：金额在查询侧派生，`total_cost` 永远不写。理由：单价是**部署事实**不是运行事实；写死了「历史金额该按旧价还是新价算」就只剩一种答案；
- **被推翻**：账单是按**当时那一版价目表**开的 —— 查询侧现算会让历史运行的金额跟着今天的价变，**与账单永远对不上**；峰谷价更逼着把「哪一刻」固定下来（判据是运行的开始时刻，只有 run 行知道）；
- **新口径**：收尾那一刻算好写死；`total_cost` 改**可空**（NULL = 没算出来，0 = 真的花了 0 元）。

**面试官可能问**：

- *「写死了就不能重估了？」* → 对，这是**认下的代价**：要做「按今天的价重估历史」，那是另一条路（另存一份），不是改这一列。
- *「价格表变了怎么办？」* → 新运行用新表，历史运行不变 —— 这正是对账要的语义。
- *「为什么不用浮点？」* → 金额走 `Decimal`，量化 6 位，JSONB 里也存**字符串**（JSON 的数字是浮点，而钱不能过浮点）。
- *「为什么三档不是四档（未命中/命中/输出/推理）？」* → 推理是**输出档的明细**，上游放在 `completion_tokens_details` 下（schema 上就是分解），单乘一遍会**系统性偏高**，而偏高与偏低都不会报错。落法是 `cost_of` **连这个参数都不收**。

### 4.3 脱敏的两个接口：一个主手段 + 一个「不假装兜得住」

**怎么讲**：

- 两个方法**不是重复**：`redact_fields` 是**主手段**（知道它是什么，就按它是什么打）；`redact_text` 是**最后一道**（按形状认，**兜不住的如实留着**）；
- 判据是「换成 code agent 还能用吗」：手机号 / 邮箱 / 身份证 / 银行卡是**跨业务**的形状 → 框架；「哪个字段敏感」只有业务知道 → 业务名单；
- **顺序是先堵实证的洞，再给通用机制**（核实发现三个泄漏点全在业务侧，框架侧 11 处日志调用没一处打过用户数据）。

**面试官可能问**：

- *「正则怎么避免误伤？」* → 只上「确定知道形状」的；订单号那种**本项目自己发的编号不猜**（猜错了会误伤，而误伤的表现是日志里该有的东西没了）。要让某一格**一定**不留原文，声明 `WIPE`。
- *「中文日志里 `\b` 能用吗？」* → **不能**。中文在 Python `re` 里也算 `\w`，`手机号13800000003` 里汉字与数字之间没有词边界 —— 用 `\b` 的规则会**静默失效**。用 `(?<!\d)` / `(?!\d)`。
- *「声明写错了会怎样？」* → **构造期就报**。因为**脱敏失效的表现不是报错，而是静默地漏**。
- *「为什么不留演示开关？」* → 安全反模式（ADR-0003 已否过一次）。**名单是代码，不是配置。**

### 4.4 690 ms：把「慢」定位到一行构造函数

**怎么讲**：

1. 现象：Django 侧每次转发都慢，而且**落在首字之前**（用户先看到一次空转）；
2. 定位：`httpx.Client.__init__` 为主传输 + **两个代理传输**各建一个 SSL 上下文，`ssl.create_default_context()` 里的 `load_verify_locations`（读系统证书库）本机一次 **~310 ms**，三次 ~930 ms；
3. 而上游是 `http://127.0.0.1:1007`，**那些上下文一次都用不上**；
4. 修法前先**回应既有理由**：原注释写着「Django 没有可靠的进程退出钩子」—— 那条理由对**多 worker** 成立，而本项目部署是**单进程**（框架侧同一句前提），所以 `atexit` 是可靠的；
5. 结果：**690~970 ms → 2~16 ms**。

**面试官可能问**：

- *「共享客户端的新失败面呢？」* → 池子里那条闲置连接可能已经死了。实测发现 **httpx 自己认得出**（httpcore 除了 keepalive 到点，还会看那个 socket 是不是已经「可读」= 对面关了连接），认出就换一条新的。**唯一漏得过去的是「请求恰好落在上游咽气那一瞬」** —— 而那一瞬上游正不在，重发换来的还是「联系不上」。
- *「那你为什么不加重试？」* → **写完又撤了**。量下来没有任何用户能看见的差别，多一层代码、多一个「到底发了几次」的疑问。**别为了兑现规划去改一个不划算的东西。**
- *「超时怎么配？」* → **不设在客户端上**，每个调用点自己传（三条路耐心不同：120 秒 / 10 秒 / 10 秒）。客户端上那份默认值一旦被谁当成兜底依赖上，「取消只等十秒」就悄悄没了。

### 4.5 竞态闸门：ticket 与布尔防的不是同一件事

**怎么讲**：

- `finished` 这个布尔只能区分「**聊没聊过**」；它防不了「两次并发加载谁更新」；
- 修法是递增 ticket：取号 → 回来比对 → 旧的丢掉。**同期还发现失败路径的判据不能混**（一处因为不用 `finished` 而漏过一次，要一起改成 ticket 口径，否则两道判据会打架）；
- 提交按钮那条是**另一件事**：加载期间锁输入区，**只留 `syncComposer` 一个出口**（三个锁源各自会变，各写一遍就是「谁后写谁赢」）。

**面试官可能问**：

- *「为什么不用 AbortController？」* → 同思路。没上是因为**保留旧响应成本极低**（一次 JSON），而取消要多一条错误分支；闸门三行、边界清楚。
- *「怎么验的？」* → 人为把三段延迟成 6s / 4s / 2s，快速连点 A→B→C：修复前三段叠一屏（`会话C…会话B…会话A…`），修复后只剩 `会话C…`。

## 5. 边界与欠账（「你哪里没做好」的标准答案）

> **这一节的用法**：面试官问短板时，**不要答「我没做」**，要答「**我知道它没做、为什么没做、什么时候该回头做**」。L3a 的收口片把每条欠账都归了属（`L3b` / `L4` / `#38` / `单开一片` / `不做`），**没有一条是「待定」** —— 这张表本身就是答案。

### 5.1 合规：「你这套东西能对外吗」→ ADR-0016

**问题**：轨迹表保留了 `arguments` 与 `result` 的**原文**（订单号、地址、余额进 PG），而"删除会话"是**软删**（ADR-0007，行一条都不动）。两条合起来就是：**用户以为删掉的东西，库里查得到。**

**标准答案（ADR-0016 的原文口径）**：

> 面试官问"你这套东西合规吗"，正确答案不是"我做了脱敏"，而是"**我知道哪一层该脱敏、哪一层必须留原文，也知道软删在这里意味着什么，以及它变成合规缺口的确切位置**"。

| 层 | 装什么 | 谁管 |
|----|--------|------|
| 展示（浏览器看到的帧） | 只有 `label`（"正在查询订单"） | ADR-0003，`redaction.py` |
| 留存（日志） | **打码后**才写 | ADR-0019，issue 29（本文 §2.3） |
| 存储（PG 里的轨迹与帧） | **原文**（订单号、地址、余额） | **ADR-0016（本节的代价）** |

**为什么存储层要留原文**（这段要能背）：

> **轨迹的用途就是回放与归因，脱了就不叫证据。** L3 的验收标准是"能回答**当时它看到了什么**" —— 如果 `result` 里的订单号被打成 `138****`，回放时看到的是一个模型**从来没有**看到过的世界，那这个工具就答不准它唯一要答的问题。DESIGN #59 说得更直白：**agent 有随机性，调试靠 trace 而非复现** —— 而 trace 的价值全在原文。

**代价认下来的三条理由**（ADR-0016 原文）：

1. ADR-0007 那条软删**本来就是这个后果**，当时记的理由对轨迹同样成立且更强（轨迹是审计材料）；
2. **「真删」是另一个量级的功能**：物理删除 + 级联衍生数据（向量 / 快照 / 轨迹）+ **能证明已删** —— 那是 `DESIGN.md` 的 **#28「数据合规删除权」**（P2，至今未做）。塞进 L3 会让 L3 的主题被一个合规域盖住；
3. **它是真实的讲述素材，不是要藏的短板。**

**什么时候该重新看**（ADR 自己写了触发条件）：**「若这套东西真的接了外部用户 —— 那时『库里有没有原文』从『讲述素材』变成『合规问题』，本 ADR 从 P2 待办升格为必做前置。」**

### 5.2 框架自己打的异常栈不过出口（#38，最大的明确缺口）

**事实**（issue 29 真机同一趟里照出来的）：

```
→ 日志: [retry] 第 1 次尝试失败, 0.5s 后重试: ... 手机号 138****0003 ...   ← 过了出口
→ 日志: 运行 ... 异常终止: ModelStatusError + traceback: ... 手机号 13800000003 ...  ← 没过去
```

**根因不在能包住的那个出口上**：框架**没有统一的日志出口** —— 五个模块各自 `logging.getLogger("charagent.<子包>")`，而 issue 29 明确「不动那套 logger 命名」（与脱敏无关，混进去会让 diff 说不清）。落点写在 ADR-0019 与 `DESIGN.md` 的 #26 落地行：**#38 一起解决。**

**顺带列在这里的同一批欠账**：

| # | 欠账 | 为什么当时没做 |
|---|------|--------------|
| 1 | 框架无统一日志出口（五个模块各拿 logger） | 取舍：与脱敏无关，混进去 diff 说不清 |
| 2 | `CharApp/minimall/client.py` 的 `_detail` / `_DETAIL_LIMIT` —— 框架异常栈那条残路的上游 | 取舍：29 号只改了 Django 侧那一处 |
| 3 | `redact_fields`（按字段名打码）**生产零调用**，约 80 行 | 取舍：交付物点名要它（为结构化日志预支），今天只有测试在喂 |
| 4 | 没有 request_id / trace_id 贯穿 | 与上面三条同一个落点 |

> **这一节怎么讲**：先给**真机日志那两行**（一个打了码、一个没打），再说根因（**不是脱敏没生效，是那条路根本不经过出口**），最后说归属（#38）。**「不是我的脱敏漏了，是框架还没有那个出口」** —— 这个区分很重要，它决定了这是一个「缺口」还是一个「bug」。

### 5.3 为什么不接 OpenTelemetry / Langfuse

**DESIGN #41 的落地栏原文**：

> **没接 OpenTelemetry / Langfuse 那一类**：本项目要的是「**给个编号就能回看这一次**」，不是一套跨服务的 span 存储。

**三条支撑**（都在 §1 里）：

1. **标准本身还没定**：GenAI semconv 在独立仓库、**Status 全是 Development**、没有一个是 stable；主站那个页面直接返回 "Moved"。
2. **标准里没有「钱」**：全文 grep `cost` 只有 3 处命中，且只肯说 token 是 `"a proxy for cost approximation"` —— **我要的那两列它不管**。
3. **项目的定位是从 0 手写框架**：接一个平台等于把「可观测」这块抽象掉 —— 而这块本身就是要展示的能力。

**但要主动补一句差距**（不然会显得在自我辩护）：

> **「不接平台不等于没有差距：跨进程传播（W3C `traceparent`）我没有，自动埋点我没有，指标与告警我没有。前两条是因为单进程部署用不上，第三条是真的缺 —— 见下一节。」**

### 5.4 三支柱缺一根：指标与告警（DESIGN #39）

OTel 定义的 GenAI 指标里，与本项目直接对应的是 `gen_ai.execute_tool.duration` 与 `gen_ai.invoke_agent.tool_calls` —— **我两个都没有。**

**表结构支持吗**：支持（`list_for_run` 之外写 SQL 就能聚合「上周工具调用成功率」）；**那为什么没做**：`DESIGN.md` 把 #39 定成 **P2**，而 L3a 的验收标准是「按 run 回看」。

**一条值得主动说的行业对照**：LiteLLM 对「有 usage 但价格为 0」的请求，除了日志 WARNING 还加了 **Prometheus 计数器 `litellm_zero_cost_requests_total`（label 含 `reason`）** —— **与我「算不出来要给原因」是同一条纪律，它做成了可告警的指标，我做成了可查询的列**。差的就是那个告警出口。

### 5.5 没有对账那一层（issue 31 明写的边界）

> 本收口**没有去拉供应商账单**（手上没有那一侧的入口），能验的是**算式与单价**。所以「对得上量级」这句话今天靠**可复算**撑着，不靠账单撑着。

**企业里这一层长什么样**（§1.3.2 的原文）：LiteLLM 的 `capture_rate = captured_spend / provider_spend`，每日 01:15 UTC 跑，对的是 OpenAI 的 `GET /v1/organization/costs`，阈值 0.9，指标 + 告警；Anthropic 那边的接口文档把用途直写成 `"Cost reconciliation: Match internal records with Anthropic billing for finance and accounting teams"`。

> **面试时的答法**：「**这一层我知道长什么样，也知道差在哪 —— 差一张 Admin key 和一个每日 job。我的验收标准定在「可复算」（算式与单价逐档能对上），而不是「与账单一致」。**」

### 5.6 Django runserver 的访问日志带订单号（单开一片）

真机日志里明文一条：

```
[25/Sep/2026 13:29:45] "GET /api/minimall/agent/orders/202607290314520000107906/ HTTP/1.1" 200 1166
```

**为什么不是「29 没做完」**：issue 29 关的是**客服服务自己**的访问日志（那里泄漏的是 `?q=` 里的搜索词），Django 这一侧当时核的是**错误日志**（403 那处只记状态码与错误码）—— **开发服务器自带的访问日志不在那一片的范围里**。也就是说这**不是漏项，是「没人问过这一侧」**。

**处置**：收口片**只记账、不修**（票据明令：收口片不改代码）。两条候选路：把 `django.server` 的访问日志按路径脱敏/关掉，或**把这类编号从路径移走**（后者会动内部端点的契约，得单独定）。

> **这一条同时是一个方法论教训**（值得单独讲）：第一次搜是搜在**追问之前**（那一刻日志里确实没有这个数），后来那趟追问把订单号带了进去。**「0 次」这种断言，要在所有流量都跑完之后再验一遍才算数。**

### 5.7 我付了的四处代价（主动说，别等人问）

| 代价 | 换来了什么 | 记在哪 |
|------|-----------|--------|
| **`**.字段名` 要递归遍历结构**（Sentry 官方默认**不递归**，理由是 `"for performance reasons"`） | 「敏感不敏感跟包了几层没关系」这条保证 | §2.3.5 |
| **连接池额度从官方默认 100/20 收紧到 10/10** | 上限压到自己能解释的量级；额度真被占满时是**阻塞等 `pool` 超时**（httpcore 原文） | §1.5.3 / §2.4.1 |
| **关掉 access log = 真的少一层观测**（如实记下） | 一次性盖住**所有**将来被带进 URL 的值 | §2.3.2 / ADR-0019 |
| **`atexit` 不是 httpx 官方写法**（官方给的是上下文管理器 / 显式 close） | 「关」确定地发生在一个已知时刻，而不是等 GC | §1.5.2 / §2.4.1 |

### 5.8 一张表收尾：L3a 之后「没有 plugins/ 目录，而且不该有」

| 要落的东西 | 落在哪 | 为什么不是插件 |
|-----------|--------|--------------|
| 工具调用事实 | 记录层（`ConversationRecorder`） | 它是**控制状态**的宿主（挂起那条 `needs_approval` 必须在挂起前落库），而 `fire` 类钩子的异常只记一笔、工具照跑 —— 兜不住 |
| 成本分量 | `runs` 五列（ADR-0005 建的）+ 收尾那一拍折算 | 写入路径不需要新代码 |
| 日志脱敏 | `Redactor` 协议 | 它是**一个被调用的函数**，不是挂载点 |

> **第一个框架侧的真实 hook 注册方出现在 L3b**（业务注册的那条「需确认」裁决走 `BEFORE_TOOL_EXECUTE`）。**面试时这句话的正确说法是**：**「这个阶段我没有新增插件 —— 这是我核实之后的结论，不是漏项。三件事各自落在既有的接缝上。」** 能说清「我没做 X 因为不需要」，比「我做了 X」更像工程判断。

---

## 6. 可能被追问的问题与答法（速查）

| 问题 | 一句话答法 |
|------|-----------|
| 你为什么不用 OpenTelemetry / Langfuse？ | 这事我核实过并写进了 DESIGN：本项目要的是「给个编号就能回看这一次」，不是一套跨服务的 span 存储。接一个平台会把「可观测」这块抽象掉，而这块本身就是要展示的能力。**但我知道差距在哪**：trace_id 不跨进程、没有自动埋点、没有指标与告警（#39 未做）。 |
| 你这套能上生产吗？ | 分三块说：**轨迹与成本**这一块结构是对的（写时机、幂等、口径都经得起推敲）；**日志**那块有明确缺口（框架异常栈不过出口，已知、已归属）；**合规**那块我认下了代价（ADR-0016：库里留原文，删除是软删）—— 触发条件是「对外提供删除入口」那天。 |
| 「当时它看到了什么」你怎么答？ | 三层：**账本**（全量原文，append-only）、**视图**（每帧 `metadata.view` = 那一轮真的发出去的那份）、**轨迹**（每次工具调用的参数原文 + 结果原文 + 耗时 + 状态）。前两层的口径在 L2.5，第三层是 L3a 落地的。 |
| 金额算不出来怎么办？ | 十种原因分开报，**绝不报 0** ——「报 0 比报不出来更糟，因为 0 看起来像个答案」。能在启动时就查出来的（配置写错 / 日历过期）**拦住进程启动**，不接活。 |
| 你这个 `trace` 和生产里的可观测平台差在哪？ | 差在**聚合与告警**：我没有「上周工具调用成功率」这种 SQL 之上的东西（表结构支持，`list_for_run` 之外没做），也没有实时面板。它不是漏项，是优先级 —— L4 的评估要的是「按 run 回看」，不是「按时间聚合」。 |
| 工具调用失败会不会漏记？ | 不会。**失败与被护栏拒绝的调用都各有一行**（`status = failed`，`result` 是拒绝原因）—— 它们是「模型想做什么」的证据，不能漏。有专门一条用例钉住。 |
| 你的幂等是怎么做的？ | 两个层次：**主键派生**（`(run_id, index)`）保证同一行写两次落在同一行上；**`ON CONFLICT DO NOTHING` + `set_status`** 保证「建行不改已有结论、推进可以重复」。收尾那一趟是**补齐**而不是「重写」。 |
| 你怎么知道你的断言是有效的？ | 一个反例：我的假库不认 `ON CONFLICT DO NOTHING`，于是幂等用例是**假绿**（同一批写两次在假库里出两行）。修了假库之后那条用例才会在退回旧写法时变红。 |
| 「0 次」这种断言怎么才算数？ | 要在**所有流量都跑完之后**再验一遍。真机上我第一次搜是在追问之前（那一刻确实没有），后来那趟追问把订单号带了进去 —— 这条教训写在收口票据里。 |
| 你的表和 OpenTelemetry 的 GenAI 约定对得上吗？ | **能对上大半**：工具 span 的四个属性（`gen_ai.tool.name` / `.call.id` / `.call.arguments` / `.call.result`）与我的四列一一对应；token 五个属性里四个能映射（我缺 `cache_write`，因为 DeepSeek 不分这一档）。**对不上的只有两层**：没有 trace_id / span_id 那层外衣（单进程用不上跨进程传播），以及**金额标准里压根没有** —— 那是我在标准之外补的。 |
| 你的「用输入总量减命中」是自创的吗？ | **不是，这是行业公认必须做的一步**。Langfuse 官方管它叫 **inclusive → exclusive 桶换算**（原文：`"Some provider counts are inclusive... must be converted into exclusive buckets before they are stored."`），给的例子与我这段代码做的是同一件事；LangSmith 的计费算例里也写着 `(20 - 5)`。根因是供应商上报口径不同：**OpenAI 的输入计数包含 cached tokens，Anthropic 的是分开报的**（LiteLLM 原文）。 |
| 历史金额为什么不能重算？ | 三家官方口径一致：**LangSmith 原文 `"Backfilling model pricing changes is not supported."`**、Langfuse `"updated defaults apply only to new generations"`、LiteLLM 靠对账比率暴露偏离。而 LangSmith 的 **Model Activation Date**（「只对该日期之后的 run 生效」）与我的「按运行的**开始时刻**判峰谷」是同一个手法 —— 把「用哪一版价」绑在时间上。 |
| 你知道业界怎么对账吗？ | 知道：**LiteLLM 的 `spend_capture_rate = captured_spend / provider_spend`** —— 每日 job 拿自己的消费表对厂商账单 API（OpenAI `GET /v1/organization/costs`），阈值默认 0.9，出指标与告警。**我没做这一层**，因为我手上没有那张 Admin key，我的验收标准定在「可复算」。 |
| 你的日志脱敏对应哪条规范？ | 两条：**CWE-598**（URL 里带敏感数据，Mitigation 就是挪进 request body）与 **ASVS 14.3.2 / 14.2.2**（生产必须关 debug、「演示用」的配置必须移除）。**有一条我特意不引**：「字段级比正则靠得住」这句话我**没找到任何一手规范依据** —— 我只引四家的实现事实（Sentry 按 key 名比对、AWS 自定义标识符被硬限 ≤200 字符）加自己的判据。 |

---

## 7. 一页纸速记（面试前 5 分钟看这个）

```
L3a（可观测）= issues 27–31，2026-09-24/25 落地，09-25 真机验收

27 轨迹      charagent_tool_calls 的第一个生产写入方
             外键逼出「产生即落库」：id 派生 (run_id, index) + 每轮两拍
             两笔写：先 pending 建行 → 再推进终态（都幂等）
             真机：库里边跑边有（2.75s 提问行 / 3.54s 调用行 / 4.47s 答复行）
             坑：说明行抢 wire 编号 / flooded 没推进 / 假库不认 ON CONFLICT

28 成本      金额在收尾那一刻算好写死（推翻前一天的「查询侧现算」）→ ADR-0018
             六档峰谷 + 中国节假日日历（真数据：09-25 周五但休息）
             reasoning 不重复计价（三条证据 + 参数位直接不收）
             十一种「算不出来」的原因，绝不报 0
             trace <run_id>：只读库、打算式、原样 JSON
             真机：¥0.001283 / ¥0.001640 / ¥0.000557（可复算）

29 脱敏      先堵三个实证泄漏点，再给通用机制
             ① uvicorn access_log=False（搜索词走 ?q=）
             ② _detail 删掉（不再截上游正文进日志）
             ③ 装配处包 writer（重试提示过了出口）
             Redactor：redact_fields（主）/ redact_text（最后一道）
             四条规则 + (?<!\d) 而不是 \b（中文坑）
             + 发现 Django runserver 的访问日志带订单号（记账不修）

30 搭车      690ms 定位到 httpx.Client 构造（三个 SSL 上下文 × 310ms）
             → 进程级 _CLIENT + atexit + 超时逐次传 + 不做重发（量过）
             690~970ms → 2~16ms
             前端 historyRequests 闸门 + syncComposer 唯一开关出口

31 收口      五条真机验收 + 欠账清点（每条有归属）+ 文档同步
             没有 plugins/ 目录，而且不该有（核实后的结论）

三句话：产生即落库 / 金额收尾写死 / 脱敏分主次

—— 行业侧的十个锚（被追问时用）——
• OTel GenAI 约定至今全是 Development（主站页面返回 "Moved"，迁到独立仓库）
• 工具 span 四属性 = 我的四列：gen_ai.tool.name / .call.id / .call.arguments / .call.result
• token 五属性四个能映射；缺 cache_write（DeepSeek 不分这档）
• OTel 全文 grep「cost」只 3 处命中，只肯说 token 是 "a proxy for cost approximation"
• cache_read / reasoning 报的是 input / output 的 subsets（所以 reasoning 不重复计价）
• 「总量 - 命中」= 行业公认的 inclusive → exclusive 桶换算（Langfuse 原文）
• 根因是供应商口径不同：OpenAI 输入含 cached，Anthropic 分开报（LiteLLM 原文）
• 历史金额不回填：LangSmith "Backfilling ... is not supported" / Langfuse "only to new generations"
• LangSmith 的 Model Activation Date = 我的「按运行开始时刻判峰谷」
• LiteLLM 的 spend_capture_rate = 我明确缺的那一层对账
• CWE-598（URL 带敏感数据，Mitigation = 挪进 request body）
• ASVS 14.3.2 / 14.2.2（生产关 debug、「演示用」配置必须移除）
• React 官方：race condition 的两条路是 "either abort the fetch or ignore its result"
• httpx 官方：a single global client instance 是正当写法；atexit 不在官方口径里

—— 我明确缺的三样（主动说，别等人问）——
① 指标与告警（DESIGN #39）：没有 gen_ai.execute_tool.duration 那一层面板
② 日志的关联 id（DESIGN #38）：框架五个模块各拿 logger，没有统一出口
③ 对账：没有 capture rate / 每日 job / 厂商账单 API
```
