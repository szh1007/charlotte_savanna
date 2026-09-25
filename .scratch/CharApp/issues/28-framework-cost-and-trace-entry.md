# 28 · 框架侧：成本口径 + `trace` 只读入口

**Status:** done

**Type:** task

**Blocked by:** issue 27（工具调用轨迹落库）—— `trace` 要列的那张表由它来填

**上游:** `CharAgent/docs/DESIGN.md` #34（成本追踪：按 task 归因）/ #35（Token 计量与成本预估）/ #41（trace 的 span 层级）；`CharApp/docs/PLAN.md` §5 的 L3a 段（验收："CLI 能列出每一次工具调用并报出这次运行花了多少"）

## 现状（2026-09-24 核实）

**数据源全都到位了，缺的只是"有人算、有人报"**：

| 数据 | 落在哪 | 状态 |
|------|--------|------|
| 全 run 累计总量 | `charagent_runs.total_tokens`（NOT NULL default 0） | 已写 |
| 五个**分量** | `input_tokens` / `output_tokens` / `reasoning_tokens` / `cache_hit_tokens` / `cache_miss_tokens`（`db/schema.py:205-233`，全可空） | 已写（ADR-0005 建列，recorder 经 `asdict(RunFacts)` 灌入，`recorder.py:439-444`） |
| `prompt_version` | `charagent_runs.prompt_version` | 已写（ADR-0005 顺手兑现） |
| `total_cost` | `charagent_runs.total_cost`，`Numeric(14,6)` **NOT NULL default 0** | **从来没人写过非零值** |
| 单次调用的 usage | `on_model_call` 的 AFTER 相载荷里带 `usage` + `elapsed_ms`（`loop.py:729-738`） | 已具备，**但没有任何插件注册到这个点** |
| **单价 / 折算金额** | —— | **全仓零代码**（搜 `price` / `cost` / `单价` 只命中注释与文档） |

## 决定一：金额在**查询侧**派生，`total_cost` 继续不写

> **⚠️ 已推翻（2026-09-25）**：改成「收尾那一刻算好写死」。原话与理由见下面的
> 「改判记录（2026-09-25，第二版）」，落定口径见 **ADR-0018**。本节留作当时的记录。

这不是"这一片偷懒"，是 **ADR-0005 已经定过的口径**的必然推论：

> 「**成本只加归因，不改账**」……「**减出来的数会撒谎**」……「要算钱就按三档单价分别乘 —— 都由那 5 列加 `prompt_ref` 算得出来，**不需要在写入路径上做减法**」

ADR-0005 当时顺手否掉了"把 `total_cost` 也算出来"（原文：「总价会变，留到 L3」）。**到了 L3，结论不变，只是理由现在能说全了**：

- **单价是部署事实，不是运行事实**。同一行 `runs` 记录的是"花了多少 token"，而"值多少钱"取决于**查的时候**用哪一版价目表 —— 供应商调价之后，历史运行的金额该按旧价还是新价算？两种答案都有道理，而**写死在行里就只剩一种**（写的那一刻那一种），且事后无从分辨。
- **分量是原始事实，钱是解释**。这与帧 / 视图 / 记录三条表示是同一个思路（ADR-0008）：原始的那份留着，解释的那份随口径变。

**所以本片要做的不是"填上 total_cost"，是给它一个交代**：
- 那一列的注释补一句「**由查询侧按单价派生，写入路径永远不填**」（`db/schema.py:235-241`）
- 这要**一条迁移**（`0003_<slug>.py`，`down_revision = "0002_thread_management"`）—— `tests/test_db_alembic.py:241` 的门要求代码侧与库侧逐列零差异，**改注释也算差异**
- 不补这条注释，下一个读代码的人一定会把它当成"漏了"然后"修好"它 —— 那正是本决定要防的

## 决定二：单价表由**部署侧**给，框架只提供机制

计价逻辑是通用的（换成 code agent 照样用），**单价不是**（deepseek 的价目表与别人无关）。所以：

- 框架侧新增一张 **`模型 → 三档单价`** 的映射与一个加载函数（环境变量或一个小配置文件），**默认空**
- **单价缺失时报"未配置单价"，绝不报 0** —— 与 ADR-0005 那句"一个把 2% 说成 47% 的指标比没有指标更糟"同一条纪律：**报 0 比报不出来更糟**，因为 0 看起来像个答案
- **三档**：输入（未命中缓存）/ 输入（命中缓存）/ 输出。ADR-0005 给了实测口径的锚（"缓存命中输入单价是未命中输入的 1/50、输出价的 1/200"）—— 具体数值由部署侧填，框架不内置任何一家的价目表

**实施时要核一件事**：`Usage.reasoning_tokens` 是否**已含在** `output_tokens` 里（读 `model/parse.py` 的解析处与 `model/utils/types.py:49-68` 的字段注释）。含了就不能重复计价 —— 这一条不核清楚，金额会系统性偏高，而**偏高与偏低都不会报错**。

## 决定三：`trace` 做成独立只读模块，不是斜杠命令

| 候选落点 | 为什么不是它 |
|---|---|
| `client/utils/commands.py` 加一个 `Command` 成员 | 那是**交互式斜杠命令**（`RESUME` / `HISTORY` / `HELP` / `QUIT`，`commands.py:35-41`）。`trace` 要的是"给一个 run_id，打印，退出" |
| 塞进 `client/app.py` 的选项 | `app.py` 是交互式 REPL 的主体（`main()` 已到 `:624-695`），再塞一个用完即退的模式会把两种生命周期搅在一起 |
| **独立模块 `CharAgent/client/trace.py`** | 与仓库既有惯例一致（`python -m CharAgent.client` 经 `client/__main__.py`），`pyproject.toml` 里**本来就没有 `[project.scripts]`**，不需要新增打包配置 |

用法：`python -m CharAgent.client.trace <run_id>`。

### 打印什么

**第一档（本片必做）**——兑现 PLAN 的验收：

```
run  <run_id>
  thread / tenant / user     ← charagent_runs
  status / model / prompt_version / 起止时刻 / 耗时
  tokens: 输入 x（命中 y / 未命中 z）· 输出 w（含推理 v）· 合计
  金额:  ¥x.xxxx        ← 单价未配置时打「未配置单价（分量见上）」，不打 0

工具调用（N 次）
  #  工具名          状态        耗时     参数（原样 JSON，超长截断）
  1  get_my_order    succeeded   142ms    {"order_no": "..."}
  2  request_refund  failed      31ms     {...}
      └ result: 这一单已经申请过退款
```

**第二档（同片可做，不阻塞验收）**：`--view` 开关，展开帧的 `metadata.view`（那一轮**真的发出去**的是什么）与 `estimate_drift` / `cache_hit_ratio`。它依赖读 `charagent_checkpoints`，与第一档是两条查询路径 —— **做不完就把这一档挪到 L3a 收口之后**，别让它挡住验收。

## 为什么 L3a 里**没有**可观测插件

`PLAN.md` 早先的措辞提过 `CharAgent/plugins/observability`；核实之后这个包**不需要建**：

| 要落的东西 | 落点 | 为什么不是插件 |
|---|---|---|
| 工具调用事实 | `ConversationRecorder` | 它是**控制状态**的宿主（`needs_approval` 在挂起前必须落库），而 `fire` 类的钩子异常只记一笔、工具照跑 —— 兜不住 |
| 成本分量 | `runs` 五列（**已经在了**） | 写入路径不需要新代码；折算在查询侧 |
| 日志脱敏（issue 29） | `Redactor` 协议 | 它是**一个被调用的函数**，不是挂载点 |

**第一个框架侧的真实 hook 注册方出现在 L3b**（业务注册的那条"需确认"裁决走 `BEFORE_TOOL_EXECUTE`）。这条要如实写进 L3a 的收口记录 —— "本阶段没有新增插件"不是漏项，是核实后的结论。

## 交付物

| # | 内容 |
|---|------|
| 1 | `CharAgent/client/trace.py`（只读入口，`python -m CharAgent.client.trace <run_id>`）+ 它自己的 DB 连接（`CHARAGENT_DB_DSN` / `PgDatabase`，与 `_recorder_for` 无关） |
| 2 | 单价表机制（三档、默认空、缺了不报 0）+ 按 `runs` 五列折算的函数 |
| 3 | `total_cost` 列注释补"由查询侧派生"（**带一条迁移 `0003_*`**） |
| 4 | 用例：折算函数（三档分别乘、命中与未命中分开、推理分量不重复计）；单价缺失时的输出文案 |
| 5 | （可选）第二档 `--view` |

## 验收

> 这一份是 **2026-09-25 第二版**的口径（金额改成收尾写死 + 峰谷六档）。第一版那几条
> （「`total_cost` 仍是 0」「单价未配置时报未配置」）已被下面的改判取代，原文留在
> 改判记录里。

- [x] `trace <run_id>` 列出**每一次工具调用**：工具名 / 参数（原样 JSON）/ 结果 / 耗时 / 状态
- [x] 同一个入口报出这次运行的**三档用量与金额**；金额与算式都是**收尾那一刻算好写进库里**的（`trace` 不重算，也不需要任何环境变量）
- [x] 金额与供应商账单对得上**量级**：真机 3666 token 的一趟 = **¥0.001189**（谷价，官方已发布单价），算式逐档可验算
- [x] 峰谷两套价：六档与三档可混用；**用了六档就必须给规则**（不给当场报错）；按**运行的开始时刻**判，工作日按中国日历（法定节假日不算、调休上班的周末算）
- [x] 算得出来时按**当前列重算**（不累加）；**算不出来时一个字节都不动** —— 不写 0、也不把已有金额清掉；状态推进碰不到那两列；比 1e-6 还小的花费按「存不下」记，不写一个撒谎的 0
- [x] `total_cost` 改成**可空**（并把旧的 `DEFAULT 0` 丢掉）+ 新增 `total_cost_detail`（JSONB：哪套价 + 三个单价 + 三档用量，或「为什么没有金额」）；列注释写清口径
- [x] `alembic check` / `tests/test_db_alembic.py` 的门通过（代码侧与库侧零差异，**含注释与可空性**）
- [x] 业务侧**进程启动自检**：价目表写错 / 日历判不了今天 → 起不来（两个入口共用一处装配）
- [x] `ruff check` / `ruff format --check` 干净；现有用例不受影响（CharAgent 1200 · CharApp 201）

## 改判记录（2026-09-25，第二版：金额改成收尾写死 + 峰谷六档）

用户在这天推翻了第一版的「决定一」，并加了两条要求。原话与处置：

| 用户原话 | 实际怎么做 | 为什么 |
|---|---|---|
| 「`total_cost` 应该在运行结束的时候就算出来啊，怎么可能想查的时候再算，万一之前是一个价，过段时间查，供应商价格变了，价格不就对不上了吗」 | **推翻决定一**：金额与算式在收尾那一刻算好写进 `charagent_runs` 两列，`trace` 只读库 | 账单是按**当时那一版价**开的；查询侧现算会让历史金额跟着今天的价变，与账单永远对不上。ADR-0005 自己写着「不把价格写进运行路径，**留到 L3**」—— 这就是那次回看（记在 **ADR-0018**） |
| 「三档表换成六档表，按 run 的开始时间算，且只算工作日，每天 9-12 点 + 14-18 点为峰」 | `CHARAGENT_MODEL_PRICES` 改成三段（`timezone` / `peak_windows` / `models`），模型可写 `peak`+`valley` 两套或三档（全天一价）；判定用**开始时刻**，峰窗 `[09:00,12:00) ∪ [14:00,18:00)` | 判定窗口取半开区间（9 点整算峰、12 点整算谷）—— 「9 点到 12 点」有四种读法，挑一种钉住比事后争论便宜 |
| 「只算工作日」 | 用 **`chinesecalendar`**（新增依赖，可选组 `charagent[pricing]`；仓库 requirements 无条件带上） | 真数据摆着：**2026-09-25 是周五但休息日**、**2026-09-20 是周日但调休上班** —— 按「周一到周五」硬判这两天都会算错，而错的金额看起来很正常 |
| 「把输出文本里的汉字换成 hit miss output」 | 用量行与算式行的档名统一成 `cache_miss` / `cache_hit` / `output`（与配置里的键一套词） | 一套词：屏幕、配置、列名三处对得上 |
| 「总之要在 run 结束的时候就算出来入库」+「写入后不能被改成 0.000000」 | `total_cost` 只在算得出来时写、**永不覆盖**（`finish` 的 UPDATE 里那一列只在 `total_cost is not None` 时出现）；明细只在金额还空着时才写原因 | 「没算出来」与「花了 0 元」是相反的结论；已有金额不能被任何一次重写弄丢 |
| 「业务侧调用使用 agentloop 功能时，加一段校验，没更新就直接报错不允许启动进程」 | `ensure_pricing_ready` / `load_pricing`（框架）+ 业务侧 `MinimallService.__post_init__` 在装配那一刻调它；错误类型进两侧的启动错误列表 | 记录员构造时也会自动验一遍（兜底），但**进程启动**那一刻只有业务侧拦得住 |
| 「总价如果超过一次收尾要累加」（他自己随后撤回） | **不累加**：按当前列重算 | 五列是**累计值**，累加会把同一笔钱算两次；「钱不丢」的保证在列（只增不减），不在加法 |

## 改判记录（2026-09-24，实施期）

| 票据原话 | 实际怎么做 | 为什么 |
|---|---|---|
| 「单价表……（环境变量或一个小配置文件）」 | **环境变量**（`CHARAGENT_MODEL_PRICES`，值是一张 JSON 映射） | 全仓的配置只有一条读法（都读环境变量，见 `db/config.py` / `checkpoint/config.py` / `model/utils/config.py`）。配置文件要额外回答「放哪、提不提交」两个问题，而它换来的只是「不用在 .env 里写一行 JSON」—— 不抵 |
| 打印布局里的「金额: ¥x.xxxx」 | **6 位小数**（`¥0.001212`），与 `total_cost` 那一列 `Numeric(14, 6)` 同标度 | 4 位时一次小运行会显示 `¥0.0000` —— 那正是本片要躲开的「看起来像个答案的 0」。列是 6 位，展示跟着列走 |
| 「参数（原样 JSON，超长截断）」 | 原样打印，**不走 `render.format_arguments`**（那一个会解析并重新序列化，还会给畸形 JSON 加标注） | `charagent_tool_calls.arguments` 的列注释就是这么定的：畸形 JSON 是自纠错路径的信号 (#2)，解析了反而丢证据。只读入口不该替库里那份事实做加工 |
| （未提）金额下面多打一行算式 | 加了 `└ 未命中 400 x ¥2/M + 命中 0 x ¥0.5/M + 输出 59 x ¥8/M` | 「三档用量与折算金额」这条验收的正面兑现：只给合计的话，金额对不对只能信框架 |
| 「第二档（同片可做，不阻塞验收）」 | **做了** | 一行查询 + 一个渲染函数；真机跑出来只有「没落视图」（见下面那条边界），但读库那条路是通的（pg_db 用例钉住） |

## 实现记录（2026-09-24 完工）

**一句话**：`total_cost` 继续留 0 并写清了为什么（带一条只改注释的迁移）；价目表从环境变量来、默认空；`python -m CharAgent.client.trace <run_id>` 把一次运行的工具调用与三档用量摊开，能算钱就算、算不出来就说清缺什么。

| 交付物 | 落在哪 |
|---|---|
| 1 | `CharAgent/client/trace.py`（`main` / `parse_argv` / `load_trace` / `render_trace` + `--view`）；自己的 `PgDatabase`，与 `client/app.py` 的 `_recorder_for` 无关 |
| 2 | `CharAgent/db/cost.py`：`PriceTable.from_env` + `ModelPrice`（三档，单位「每百万 token」）+ `cost_of`（三档各乘各的，量化到 6 位）；缺单价 / 缺分量给 `CostGap` 原因，**不给 0** |
| 3 | `db/schema.py` 的 `total_cost` 注释 + `alembic/versions/0003_cost_derived_on_read.py`（`down_revision = "0002_thread_management"`）；`db/entities.py` 的 `Run` docstring 同步 |
| 4 | `test_db_cost.py`（24 条，纯函数）· `test_client_trace.py`（31 条，纯渲染/参数）· `test_client_trace_db.py`（7 条，`pg_db`，真库接线） |
| 5 | `--view`：读帧的 `metadata.view`（库里那份 JSONB 的原样），打估算 / 漂移 / 命中率 / 压缩账 |

**票据点名要核的那件事（核完了）**：`Usage.reasoning_tokens` **已含在** `output_tokens` 里，**不能重复计价**。三条证据：①上游把它放在 `usage.completion_tokens_details` 下 —— 这个层级在 schema 上就是 `completion_tokens` 的分解；②实测样本 `tests/fixtures/llm/tool_path_thinking.json` 第一条 `completion_tokens = 59` 而其中 `reasoning_tokens = 16`；③同一条样本里上游自己给的 `total_tokens` 恰好等于 `prompt_tokens + completion_tokens`（459 = 400 + 59）—— 若推理是另计一笔，上游自己的总数就对不上。落法：`cost_of` **连这个参数都不收**（传进去直接 TypeError），用例 `test_reasoning_tokens_never_change_the_amount` 再从展示层钉一遍。

**实施期发现并处理的四处**（都不是顺手，是核对时真撞到的）：

1. **单价表少了「时段」这一维**：deepseek-flash 的真实价目表是**峰谷两套**（空闲 0.02 / 1 / 4，高峰 0.04 / 2 / 8，每百万 token），而三档表只能装一套。本片**认下**：部署侧填哪一套，屏幕上就打哪一套的算式（`└` 那一行把三个单价原样写出来，读的人能看出这是哪一套）。要按时段自动选价得给 `ModelPrice` 加一维，那是另一个决定。
2. **CLI 从来不打印 `run_id`**：所以「给一个 run_id」这件事在框架自己的命令行里**拿不到**（真机验证时是查库拿的）。这是本条验收在实际演示上的断点：跑完一次运行，用户手上没有那个编号。
3. **框架 CLI 不配压缩策略，于是真机跑出来的帧没有视图**：`--view` 会如实打「没落视图 (没配压缩策略, 或那一轮没有模型调用)」。第一版这里只写了「这一轮没有模型调用」—— 真机一比才发现那是在替读的人挑一种原因（他会去查模型调用记录，而那里一切正常）。已改。
4. **`0ms` 与「没计时」在屏幕上是两回事**：列上 `duration_ms = NULL` 打 `-`（对），但 demo 工具跑得比 1ms 快，真机打出来是 `0ms`。留着（那是库里存的那个数），记在这里备查。

**验证**：`pytest` **1133 passed**（原 1071 + 新 62，零失败）· `pytest -m pg_db`（`test_client_trace_db` + `test_db_store` + `test_db_alembic`）**60 passed** · `ruff check` = All checks passed · `ruff format --check` = 181 files already formatted · 真机一次带工具的运行（`--backend postgres`，两轮、两次工具调用、2 帧，`run_id = be02e777c6864fff895257dd92332aeb`）→ `trace` 列出两次调用与结果；空价目表报「未配置单价」；填上官方空闲时段单价后算出 **¥0.001212**（3677 token 的一次运行，量级对得上）；`total_cost` 在该库里 **3 行全是 0**（没有一行被写过非零值），列注释已经是新那一句（`alembic upgrade head` 已在本机开发库跑过，版本停在 `0003_cost_derived_on_read`）。

**关于「L3a 里没有可观测插件」这条结论**（票据末尾那张表）：实施期又核了一遍，结论照旧 —— 本片**没有注册任何 hook 订阅方**，工具调用事实仍由 `ConversationRecorder` 写（它是控制状态的宿主，`needs_approval` 必须在挂起前落库），成本分量仍只在 `runs` 五列（写入路径零新代码），折算在查询侧。**L3a 第一个框架侧的真实 hook 订阅方要到 L3b**（业务那条「需确认」裁决走 `BEFORE_TOOL_EXECUTE`）。这句连同「不是漏项，是核实后的结论」一起供 issue 31（L3a 收口）抄进结项记录。

**CodeReview 之后补的一处**（见下面处置表第 2 条）：`cost_of` 补收 `input_tokens`，用来补算缺的那一档缓存分量（未命中 = 输入总量 - 命中）—— 少了它，只给 `prompt_tokens_details.cached_tokens` 的上游（OpenAI 系的形状，而框架的模型层本来就认这种响应）金额会**永远**报「算不出来」。补算出来的那一档在算式里标「(推自输入总量)」，因为用量那行的同一个位置写着 `-`。

## 实现记录（2026-09-25 第二版完工）

**一句话**：钱不再等有人查的时候才算 —— 运行收尾那一刻按**当时的**价目表算好、连算式一起写死在运行行里；价目表支持**峰谷两套价**（按运行的开始时刻判，工作日看中国日历）；`trace` 从此只读库，业务侧进程启动时先自检一遍配置与日历。

| 落在哪 | 做了什么 |
|---|---|
| `db/cost.py`（重写） | `TierPrices`（三档）/ `PeakRule` + `TierVerdict`（峰谷裁决）/ `PriceTable`（六档或三档 + 规则 + 读失败的原因）/ `RunCost`（金额 + 算式 + 落库明细的编解码）/ `cost_of` / `ensure_pricing_ready` + `load_pricing` / `PricingNotReadyError` |
| `db/recorder.py` | 构造时读**一版**价目表并自检（抛则起不来）；`begin` 记下开始时刻（同一值写进 `created_at` 与判峰谷）；收尾那一拍算钱并写库，`record_unfinished` 带「没跑完」的原因 |
| `db/repositories/runs.py` | 建行写 `NULL`；`finish` 收 `total_cost` / `total_cost_detail` —— **只在算得出来时写金额**，明细只在金额还空着时写原因（两列永远自洽） |
| `db/schema.py` + `alembic/versions/0003_run_cost_columns.py` | `total_cost` 改可空 + 新列 `total_cost_detail`（JSONB）；迁移**改写**未提交的 `0003`（编号从 `0003_cost_derived_on_read` 改成 `0003_run_cost_columns`，标题与内容一起换） |
| `client/trace.py` | 拆掉「读价目表 + 现算」那条路：金额与算式从行里读，档名换 `cache_miss` / `cache_hit` / `output`；没有金额时按明细里那句原因打 |
| `client/app.py` | `PricingNotReadyError` 进框架 CLI 的启动错误列表（报一句人话 + 退出码 1） |
| `CharApp/minimall/service.py` | `MinimallService.__post_init__`：装配那一刻调 `load_pricing()`（只记账的进程才验）；错误类型进业务侧 `STARTUP_ERRORS` |
| 依赖 | `chinesecalendar>=1.11.0`：可选组 `charagent[pricing]`（`CharAgent/pyproject.toml`）+ 仓库 `requirements.txt` 无条件带上；`pyproject.toml` 里「依赖只有八项」那句随之改准 |
| 文档 | **ADR-0018**（新）+ `CONTEXT.md` 四个词条（用量 / 运行金额 / 峰价谷价 / 价目表）+ `.env.example` 换成新形状（含真实已发布价） |

**验证**（CodeReview 的处置也改完之后重跑）：`pytest` **1203 passed**（第一版完工时 1133）· `pytest -m pg_db`（`test_client_trace_db` + `test_db_store` + `test_db_alembic`）**71 passed** · `ruff check` = All checks passed · `ruff format --check` = 181 files already formatted · **CharApp 201 passed**（+3 条启动自检用例）· 真机两趟带工具的运行，两趟都在处置之后复跑过：`run_id = 41b48cd2b7734e7cbb519328d760cb96`（2026-09-25 02:10 +08:00 开始）→ `total_cost = 0.001189`；`run_id = 3191c7ae4d97455f9dcdc45c782237ec` → `total_cost = 0.001457`（`output 230` 里含 `reasoning 147` —— 推理没有重复计价），明细 `tier = valley`、三档逐项可验算；`trace` **不带任何环境变量**打出同一组数与同一个算式。本机开发库已迁到 `0003_run_cost_columns`（可空 + 新列 + 新注释都在库里核过）。

**这一版顺手解掉的旧边界**（第一版留下的那条）：①「单价表少了时段这一维」→ 六档 + 规则解决；②「第二档 `--view` 真机看不到东西」照旧（框架 CLI 不配压缩策略）。**仍然留着**：`run_id` 在框架 CLI 里拿不到（留给 issue 31 定）。

**给后来人的两个坑**（都在这一版踩过）：

1. **迁移改名的代价**：`0003` 改名之后，本机开发库的版本表里那条旧编号（`0003_cost_derived_on_read`）会让 alembic 直接报「Can't locate revision」。处置：把版本表那一行指回 `0002_thread_management` 再 `upgrade head`（本机已这么做；库里当时是空的，没有数据要补）。**将来提交之后再改迁移名就没这么便宜了** —— 那正是 ADR-0006「发布前才能压历史」那条的边界。
2. **`chinesecalendar` 的 CI 只测到 Python 3.12**（纯标准库、无 `requires_python` 上界），本机 3.13 实跑过 ✓；它每年 ~11 月发一版覆盖下一年，**过期后金额会集体留空**（启动自检会先拦住，报「升级依赖」）。

**留给后面几片的边界**：

- **`run_id` 的发现路径**（上面第 2 条）：要么让 `python -m CharAgent.client` 在完成行里打一次 `run_id`，要么给 `trace` 加一个「看最近一次」。两条都是**对外接口**的决定，留给 L3a 收口（issue 31）定，本片不擅自加。
- **峰谷两套价**（上面第 1 条）：`ModelPrice` 加一维时段，或让部署侧按当前时段自己换 `.env`。眼下先按后者。
- **`--view` 的真机演示**：要看到非空视图，得有一个配了 `CompactionPolicy` 的会话（业务侧 / server 侧那条路），框架 CLI 演示里看不到。
- **币种耦合**（CodeReview 提出）：屏幕上的 `¥` 是写死的，价目表却是部署侧给的 —— 换一家美元计价的供应商要连展示层那个符号一起改。眼下按「部署侧负责让两者一致」记在 `db/cost.py` 与 `.env.example` 里；要真做成配置项，等真有第二家不同币种的供应商再说。

## CodeReview（2026-09-24）与处置

两条轴各派一个 sub-agent 独立跑（Standards：仓库成文规范 + 气味基线；Spec：票据逐条核对）。结论与处置如下。

**Standards 轴**

| 发现 | 处置 |
|---|---|
| `RunCost.lines` 是没人用的公共属性（Karpathy §7.2 不实现未要求的功能；气味基线 Speculative Generality） | **已改**：删掉（评审看到的是删之前的快照） |
| `_price_of` 少 `Returns:`、`_as_price` 少 `Raises:`（系统级 CLAUDE.md §2.1 Google docstring） | **已改**：两处补齐 |
| `_calls_block` 与 `checkpoint/utils/history.py` 的 `format_history` 同形（表格对齐那段）—— 建议抽一个共用的 `render_table` | **留着**：本仓的 DRY 阈值是「重复 ≥ 3 次抽取」（issue 27 同一条判例），这是第 2 处；而抽出去要把 `format_history` 拆成「列定义 + 通用对齐器」，改的是另一个包（checkpoint）的公共件 —— 代价大于这段 20 行的重复 |
| 三档的「未命中 / 命中 / 输出」在四处各写一遍（`_TIERS` / `_COMPONENT_OF_TIER` / `_TIER_TEXT` / 展示层），顺序靠人维护 | **留着**：四处各自服务一件事（算术 / 报缺 / 显示 / 算式的标签），合成一个枚举要把展示层的标签搬进 `db/` 或让 `RunCost` 的字段名变成 `getattr` 取 —— 读起来更绕。记在这里：**若真的错了一次顺序**（用例会红）再来抽 |
| `RunCost.missing: tuple[str, ...]` 带裸列名、测试里 `thread: Any = _UNSET` 类型偏宽 | **留着**：列名是故意的（与 runs 的列同名，报缺时直接用）；哨兵那个是测试参数「没给」与「给 None」的区别，`Thread \| None \| object` 只是把 `Any` 写长 |
| `_PER_MILLION = "M"` 是个只用一个字符的模块常量（名字与值对不上） | **已改**：就地内联进 f-string |
| `TraceOptions` 放在 `trace.py` 而不是 `client/utils/types.py`（那个文件文档说是「装启动选项的对象」的家） | **留着**：那个文件的定位是「供几个行为模块共享的静态零件」，而 `TraceOptions` 只有 trace 自己用 —— 与本仓「上浮要等真实调用方撞一次」同一条口径（`client/app.py` 的 docstring 就是那条口径的来源）。等它有第二个用家再上浮 |
| 三个测试文件各建一份价目表（建议放 `tests/doubles.py`） | **留着**：三份的形状本就不同（纯函数用 `PriceTable` 对象、真库那条**必须**用环境变量原文），而重复的是**测试数据**、没有共享断言 —— 抽出去只会在三处多一个 import |

**Spec 轴**（结论：交付物 5 条兑现，票据点名的核实项已核）

| 发现 | 处置 |
|---|---|
| 交付物 2 写的是「按 `runs` **五列**折算」，而 `cost_of` 只收三档、`input_tokens` 完全没进算式 | **已改**：补收 `input_tokens`，用它补算缺的那一档缓存分量（见上面那条）；补算出的档在算式里标来路 |
| 只有 `cache_miss_tokens` 没有 `cached_tokens` 那种回退 —— 非 DeepSeek 响应会**永远**落在 `NO_TOKENS` | **已改**（同上）。顺带在 `cost_of` 的 docstring 里写清了「为什么收输入总量却不用它定价」，以及「差是负数就地放弃」 |
| 「`reasoning_tokens` 传进来会直接 TypeError」只是 docstring 里的一句话，没有用例走过 | **已改**：`test_the_function_has_no_slot_for_reasoning_tokens` |
| `client/__init__.py` 的「结构总览」没有加 `trace.py` 一行，而 `db/__init__.py` 为 `cost.py` 加了 | **已改**：补上（结构总览 + 用法示例） |
| 示例价目表 `2 / 0.5 / 8` 与 ADR-0005 记的价差（命中 = 未命中的 1/50、输出的 1/200）对不上，而 `test_db_cost.py` 的 docstring 还引用了那个比例 —— 代码在陈述一组自己数据不支持的价格关系 | **已改**：`.env.example` 与 `db/cost.py` 换成 deepseek-flash 的**已发布价**（空闲 1 / 0.02 / 4，并写明高峰那套）；测试里那张表明确写成「造出来好算的数，不是任何一家的真实价目表」，把 1/50 那句话删掉 |
| 屏幕上的 `¥` 是写死的，价目表却是任意币种 | **留着 + 记档**：写进 `db/cost.py` 与 `.env.example`（「表的数字与那个符号必须同币种」），并作为已知耦合记在上面「边界」里 |
| 门面里那 4 个 `TraceSink` / `ToolCallFact` / `ToolCallOutcome` / `message_id_for` 被算成 scope creep | **不是本片加的**：它们是 issue 27 的（本会话开始前 `CharAgent/__init__.py` 就已是 M）—— 但**两片的改动混在同一批未提交差异里**这件事是真的，正是提交前要跟人对一下的那一条 |

### 第二版（2026-09-25，金额收尾写死 + 峰谷六档）的复审

两条轴各派一个 sub-agent 独立跑。这一轮**找出 4 个真缺陷**（不是措辞问题），都已改并各带用例。

**Standards 轴**

| 发现 | 处置 |
|---|---|
| `_fill_missing_tier(..., tier)` 的 `tier` 参数一次都没用到（死参数） | **已改**：删掉 |
| 一批 Google docstring 缺节（`from_detail` / `to_detail` / `tier_needed` / `price_for` / 私有件） | **已改**：补齐。`tier_at` 那条另作处理 —— 时区名改成在 `PeakRule.__post_init__` 里验掉（**手搓的规则也当场报错**，而不是等到判的时候抛 `ZoneInfoNotFoundError`） |
| 迁移注释声称「类型 / 可空 / 默认值都原样带上」，而 `existing_server_default` 只是 autogenerate 的**比对输入**、不进 DDL | **已改（真缺陷）**：库里于是留着 `DEFAULT 0`，而代码里已经没有默认值了 —— 任何漏写这一列的 INSERT 都会拿到 0 = 「真的花了 0 元」。处置：迁移里显式 `server_default=None` 丢掉它；开发库手工补上同一步；**门里打开 `compare_server_default`**（这类漂移此前门抓不到）；再加一条直接断言「这一列没有默认值」的用例 |
| 断言过宽：`gap in (UNKNOWN_DETAIL, NO_TOKENS)` 里后一个是死的 | **已改**：收成 `is UNKNOWN_DETAIL`（并把「压根没有明细」拆成单独一条） |
| 气味：`cost_of` 的五个 token 参数与 `RunFacts` 同形（Data Clumps） | **留着**：`RunFacts` 本身就是那个包，而 `cost_of` 按**列**取参是可测、可读的窄接口（今天只有一个调用方） |
| 气味：`CostGap` 上两处 if 级联（Repeated Switches） | **留着**：受众不同（启动报错 vs 屏幕），覆盖的原因也不同（前者只管两种判不了的） |
| 气味：`tuple[()]` 当「依赖没装」的哨兵（Primitive Obsession） | **已改**：直接用 `CostGap` 表达（`bool | CostGap`） |
| 注释与实现不符：`_TIERS` 自称「唯一一处定义」，而展示层另列了一遍三个档名 | **已改**：说准（展示层那份是版式，不是同一件事） |
| 小项：测试里 `detail: dict` 没参数化 | **已改**：`dict[str, Any]` |

**Spec 轴**

| 发现 | 处置 |
|---|---|
| 峰谷判定的时刻取自记录员**内存**里的 `_started_at`：换一个实例收尾（挂起补做 / 对账补齐 / 换了进程）就落 `NO_MOMENT`，峰谷部署下金额**静默留空** | **已改（真缺陷）**：改成从运行行读 `created_at`（库里本来就有这个事实），内存那份整个删掉 —— 少一处状态，多一处对得上 |
| 「金额写进去之后不被改」这条话与实现不符：`finish` 收到算得出来的金额时是无条件写的（守卫只加在明细上） | **改的是说法，不是行为**：约定的规则是「**算得出来按当前列重算**（覆盖；五列是累计值，重算得到那一刻的总额），**算不出来一个字节都不动**」。代码本来就是这两条，是票据验收、`runs.py` 的 docstring 与 ADR-0018 三处把话说过了头 —— 都改准，并补一条用例把「同一份账重算两次得到同一个数（不是相加）」钉住 |
| `.env.example` 留着**未注释**的示例价，而同段文字写着「默认空」—— 照抄模板的人其实不经过那个默认 | **已改**：写明那是示例值（照抄即生效），删掉或留空才是「没配价」 |
| `trace --help` 还写着「金额按环境变量里的价目表算」 | **已改**：改成「金额与算式都是收尾那一刻算好写进库的，这里只读」 |
| `db/cost.py` 说「`client/trace.py` **不调用本模块**」，而 trace 正 import 它的两个类型 | **已改**：说准 —— 不算钱，只借两个类型把明细读成结构化对象 |
| 金额空**且**明细也空（进程被硬杀 / 正在跑 / 改口径之前写的）被打成「明细读不懂：老格式或坏数据」—— 把正常状态说成坏数据 | **已改（真缺陷）**：新增 `NO_DETAIL` 原因，措辞改成「库里没有这笔账（这一趟还没收尾，或它是改口径之前写的）」 |
| 三档各自先量化到 6 位再求和：比 1e-6 还小的花费会存成 `0.000000` —— 屏幕上就是「真的花了 0 元」，正是本片要躲开的那个零 | **已改（真缺陷）**：新增 `TOO_SMALL`（原始金额为正、量化后为 0 → 报「小于这一列的最小刻度，存不下」），侧着写了一条用例 |


## 备注

- **别把单价写进代码常量**：那会让"换个模型/换家供应商"变成一次代码改动 + 一次发版。
- **别在 `on_model_call` 上注册一个记账插件**：用量已经在 `runs` 五列里了（ADR-0005），再加一条写路径就是同一件事存两份。
- 与 issue 27 的分工：27 只管**把事实写进去**，本片管**把事实读出来、算成钱**。
- 与 L4 的关系：本片是 L4 评估的**读数入口**（"这次改得比上次好"要拿它比）。
