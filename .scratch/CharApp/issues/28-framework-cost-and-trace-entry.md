# 28 · 框架侧：成本口径 + `trace` 只读入口

**Status:** todo

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

- [ ] `trace <run_id>` 列出**每一次工具调用**：工具名 / 参数（原样 JSON）/ 结果 / 耗时 / 状态
- [ ] 同一个入口报出这次运行的**三档用量与折算金额**；金额与供应商账单对得上**量级**
- [ ] 单价未配置时打「未配置单价」而**不是 0**
- [ ] `total_cost` 列在库里仍是 0，且列注释说明了原因
- [ ] `alembic check` / `tests/test_db_alembic.py` 的门通过（代码侧与库侧零差异）
- [ ] `ruff check` / `ruff format --check` 干净；现有 1052 条用例不受影响

## 备注

- **别把单价写进代码常量**：那会让"换个模型/换家供应商"变成一次代码改动 + 一次发版。
- **别在 `on_model_call` 上注册一个记账插件**：用量已经在 `runs` 五列里了（ADR-0005），再加一条写路径就是同一件事存两份。
- 与 issue 27 的分工：27 只管**把事实写进去**，本片管**把事实读出来、算成钱**。
- 与 L4 的关系：本片是 L4 评估的**读数入口**（"这次改得比上次好"要拿它比）。
