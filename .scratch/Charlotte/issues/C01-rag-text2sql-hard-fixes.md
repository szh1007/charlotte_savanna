# C01 · rag_text2sql 硬伤修复与口径对齐

**Status:** done

**Type:** fix

**Blocked by:** —

**上游:** `.scratch/Charlotte/PLAN.md` §2 组 0；2026-09-30 的代码审查（结论带 file:line）

## 为什么先做这一片

这个项目要作为「RAG 工程」这条经历的**第二半**讲。它的设计站得住（元数据知识化 + Schema Linking + 执行反馈闭环），但有四处**已知会崩或与文档不符**的地方 —— 面试官真跑一次、或真点开 README 对代码，就是「造假」而不是「夸大」的区别。

**四处硬伤里有三条已读码复核过**（不是转述）。

---

## 一、会崩的两处

### 1.1 「值→列」兜底路径一走就 KeyError

`app/agent/nodes/_3_merge_retrieve.py:47-48`

```python
if _value not in retrieved_columns_map[col_id]["examples"]:
    retrieved_columns_map[col_id]["examples"].append(_value)
```

此时 `col_id` 可能**不在** `retrieved_columns_map` 里 —— 它在 `:43-44` 刚被记进 `pad_col_ids`，而真正的补列发生在 `:51-59`。**补列晚于使用。**

**这条正是 README §6.1-6 自己写的「取值路只能靠值→列兜底」那条路** —— 一走就整图崩。日志里「合并召回信息失败」0 次，说明**这条兜底从来没被真正触发过**，属「写了没验」。

**做法**：把补列提到使用之前（把 `:51-59` 的补齐循环挪到 `:38` 的循环之前），或先补齐再追加 examples。二选一，但**要有一条回归测试**：构造一个「取值命中、但该列未进列召回」的 state，断言不抛异常且 examples 里含该值。

### 1.2 SQL 执行失败静默，前端永远转圈

`app/agent/nodes/_9_execute_sql.py:22-24`

```python
except Exception as e:
    logger.error(...)
    return {"error": str(e)}
```

**没有调 `writer`** —— 前端收不到 `result` 也收不到 `error`，最后一个步骤永远停在 running。

**做法**：异常分支补 `writer({"error": ...})`（事件形状对齐 `_7_validate_sql` 与 `QueryService` 的既有口径）。回归测试断言：执行失败时**至少推出一条 error 事件**。

---

## 二、换台机器就跑不起来

### 2.1 三处 stdlib 误导入

| 位置 | 现在 | 应为 |
|---|---|---|
| `app/core/log.py:5` | `from Lib import uuid` | `import uuid` |
| `app/services/meta.py:4` | `from Lib.pathlib import Path` | `from pathlib import Path` |
| `app/core/context.py:1` | `from sentry_sdk.utils import ContextVar` | `from contextvars import ContextVar` |

IDE 自动导入把解释器的 `Lib` 目录当成了包。本机**恰好能跑**（`C:\Program Files\Python\...` 在 `sys.path` 里，`Lib` 被解析成 namespace package），换 Linux / macOS / pyenv **立即 ImportError**。

**这是「面试官 clone 下来跑不起来」的第一原因，也是现场演示的隐患**（万一换台机器）。

**做法**：三处改掉，然后**加一条 import 冒烟测试**（`import app.core.log` / `app.services.meta` / `app.core.context` 三个模块），防止再犯。同时全仓 grep 一遍 `from Lib`、`from sentry_sdk` 确认没有第四处。

---

## 三、README 与代码不符

> **⚠️ 写票时的错误，2026-09-30 实施期读码纠正**：本节初稿有**三条是从 `rag_knowledge` 串过来的**，
> 本项目根本没有这些东西 —— 「web 取 10 条 vs 5 条」「三路并行 → RRF 融合」「MCP 代码整段注释」
> 「`escape_milvus_string_utils` 未被调用」四条**全部作废**（`grep -rin "tavily|web_search|mcp" app/ main.py`
> 零命中；本项目用 Qdrant，从不涉及 Milvus）。
> **教训**：写票时对着二手摘要抄了未复核的条目 —— 与 `PLAN.md` §3 那条「读码核过前置才切票」同一条纪律，
> 这次是我没守住。

| # | README 说 | 实际 | 处置 |
|---|---|---|---|
| 3.1 | 「单图 **9 节点**」（`:24` / `:48` / `:127` / `:343` 共四处） | `app/agent/graph.py` **12 个 `add_node`** | 改成「**12 个节点 / 9 个逻辑阶段**」—— 两个数都写，别只改一个 |
| 3.2 | 「ES 文档 id 为 `字段id.取值`」（`:148`） | `repositories/es/value.py:59` bulk 写入只传了 `{"index": {"_index": ...}}`，**没传 `_id`** → ES 自动生成 | 改成如实描述 |
| 3.3 | `repositories/qdrant/metric.py` 的 `search` docstring「默认0.6」 | 签名与实际都是 **0.7**（`column.py` 那侧是 0.6，两份被抄混了） | 改 docstring |

**顺带核实后确认无误的**（不要改）：`recall_value` 那条「默认 10 条」是**对的** —— ES `match` 查询未传 `size`，默认就是 10（`es/value.py:74-77`）。

---

## 四、顺带清掉的教程痕迹

- `prompts/plan_sql.prompt` 是死代码（README `:106` 自认「暂未挂载」），且**全项目零引用**（`grep -rn "plan_sql" app/ main.py` 只命中文件名自身）→ **删**
- 四份扩展/过滤 prompt 的 few-shot 示例是 **HR 场景**（「最近三个月在职实习生的转正情况如何？」+「员工身份类型 / 在职状态 / 转正日期」），而业务是电商星型模型 → **换成电商示例**（这是最容易被认出「搬来的模板」的一处）
- `frontend/src/components/HelloWorld.vue` 是 Vite 脚手架残留、**未被任何文件引用** → **删**

---

## 五、测试骨架（最小，不是全套）

`rag_text2sql` 目前**零测试**。这一片不建测试体系（那是 C14 的事），只为**这一片修掉的东西**钉回归：

- `tests/test_merge_retrieve.py` —— 1.1 的 KeyError 场景
- `tests/test_execute_sql.py` —— 1.2 的 error 事件
- `tests/test_imports.py` —— 二的三处误导入

**做法**：建最小的 `tests/` + `pytest.ini`（`asyncio_mode=auto`，对齐仓库里 `CharAgent` / `CharApp` / `project/charplot` 的既有写法），依赖注入沿用 `app/agent/context.py` 的 `DataAgentContext`，用假 repository 顶掉 MySQL / Qdrant / ES。

---

## 验收

> 原清单里两条按 §三 的纠正作废：**「README 四处」→ 三处**（多出来的那条是串过来的）；
> **「`escape_milvus_string_utils` 二选一」→ 作废**（本项目没有这个文件）。

- [x] 1.1 的兜底路径能跑通，有回归测试
- [x] 1.2 执行失败时前端收到 error 事件，不是无限转圈
- [x] 三处误导入改掉，`tests/test_imports.py` 钉住
- [x] README 三处口径与代码对齐（12 节点 / ES `_id` / metric 阈值）
- [x] `plan_sql.prompt` 删除；四份 prompt 的 few-shot 换成电商示例
- [x] `HelloWorld.vue` 删除
- [x] `pytest` 全绿（68 个用例，含 6 条真 import 冒烟）
- [x] **从干净目录 clone 后能跑**（不是「在我机器上能跑」）—— 造了一份
      **排除 `conf/*.yaml`** 的副本实测，那里 `conf/` 是空的，**68 个用例同样全绿**

## 改了哪些文件

**改**
- `app/agent/nodes/_3_merge_retrieve.py` —— 补列挪到 examples 回填之前
- `app/agent/nodes/_9_execute_sql.py` —— 失败分支补 `writer({"error": ...})`
- `app/core/log.py` / `app/core/context.py` / `app/services/meta.py` —— 三处借道导入改回标准库
- `app/repositories/qdrant/metric.py` —— docstring `0.6` → `0.7`
- `README.md` —— 节点计数（含目录树共 5 处）· ES `_id` 口径 · prompts 目录树去掉已删项 · §8.9 的过期描述 · 最后更新
- `prompts/*.prompt` ×4 —— few-shot 换成电商示例

**新增**
- `pytest.ini`
- `tests/conftest.py`（私有配置缺席时才注入的最小替身）· `tests/doubles.py` · `tests/test_imports.py` · `tests/test_merge_retrieve.py` · `tests/test_execute_sql.py`

**移除**（移到 `D:\__WorkSpace__\Temp\rag_text2sql-c01\`，**没有真删**）
- `prompts/plan_sql.prompt` · `frontend/src/components/HelloWorld.vue`

## 实施记录

### 2026-09-30

**三处「把 bug 放回去」的核对** —— 测试全绿不等于能抓住 bug，逐个验过：

| 放回什么 | 结果 |
|---|---|
| `_3_merge_retrieve.py` 的旧顺序 | `test_merge_retrieve.py` **4 个里失败 1 个** —— 恰好是「值命中的列未被列召回」那条；另一条分叉输入（列已被召回）照过，**证明两个用例不是重复** |
| `_9_execute_sql.py` 去掉 `writer` | `test_execute_sql.py` **3 个里失败 1 个** |
| 把 stage 事件挪到 try 之后 | `test_merge_retrieve.py` **4 个里失败 1 个**（名字里带 `before` 的那条） |

**自己抓出来的一条空测试**：`test_imports.py` 最初写的是一条**运行时断言**
（`isinstance(request_id_ctx_var, contextvars.ContextVar)`）—— 实测
`sentry_sdk.utils.ContextVar is contextvars.ContextVar` 为 **True**（sentry 只是把标准库那一个转出来），
于是误导入与正确写法**断言结果一模一样**。改成按 AST 判导入：四种 `Lib` 写法全抓、
`sentry_sdk.utils.ContextVar` 也抓，而 `from contextvars import ContextVar` /
`from pathlib import Path` / `from sentry_sdk import init` 不误伤。

**「clone 后能跑」是怎么验的**：本机有私有 `conf/app_config.yaml`，所以把项目复制一份
**排除 `conf/*.yaml`**（副本的 `conf/` 目录为空）再跑 —— **62 个用例全过**。
成立靠的是 `tests/conftest.py` 里那个「只在文件缺席时才注入」的配置替身；
**生产入口 `main.py` 不走这条路**，仍然是「读不到就报错」。

**代码评审（Standards 轴）带回三条，都已修**：
① README 目录树里还漏一处「9 节点」；② `pytest.ini` 的注释把「另外四处」写成「另外三处」；
③ 一个测试的名承诺 `before` 却只断言了「事件存在」—— 改成「让工作抛错、断言阶段事件**仍然已经发出**」，并放回 bug 验过会红。

**顺带发现、本票不动的**：`conf/meta_config.yaml` 里 **AOV 的 `relevant_columns` 写的是
`fact_order.order_quantity`**，而它的描述是「成交金额平均值」、别名是「平均订单金额」——
应为 `order_amount`。那是本地私有配置（gitignored）且改了要重建索引，**归 C15**。

### 代码评审（Spec 轴）带回两条半做 + 两条真问题

**① README 只改了一半**：`:126` / `:342` 只写了「12 个节点」、漏掉「9 个逻辑阶段」
（规格要求**两个数都写**）→ 补齐，现在五处口径一致。

**② `test_imports.py` 一次真 import 都没做** —— 只有全仓 AST 扫描。于是
**`app.services.meta` 从头到尾没被执行过**，而「克隆到干净机器能 import」这句话的
证据其实只是「源码里没有那种写法」。**这两件事不等价**，是本票最实质的一条。
→ 补真 import 冒烟：`app.core.context` / `app.core.log` / `app.services.meta` /
`app.clients.es` / `app.clients.mysql` / `app.agent.graph`（后三条把 clients 与整条装配链拉起来）。

**③ 配置替身不完整**（② 的冒烟测试**当场就抓到了**）：`app/clients/*.py` 是
`from app.conf.app_config import DBConfig, app_config`，而替身只给了 `app_config`
→ 干净机器上 import `app.clients.*` / `app.agent.graph` 直接 ImportError（已复现）。
→ 替身改成**从真模块源码派生**：把 `config_file = ...` 之前的 dataclass 定义原样 exec 出来，
再按字段递归造占位实例。**不再手抄一份镜像，漂移就不可能**。

**④ 1.1 的兜底仍有一条边界**：补列的前提是该列 id 能在 meta 库查到；**查不到时仍然
KeyError、整图报「合并召回信息失败」**。这不是本票新引入的（修复前同样如此），
记在这里 —— 不假装已经全兜住。

### 在「无私有配置的干净副本」上抓到的两个真问题

本机有 `conf/app_config.yaml`，所以这两条**只有真造一个干净副本去跑才会现形**：

1. `@dataclass` 处理类时要按 `cls.__module__` 回查 `sys.modules`，**注册晚于 exec**
   就是 `'NoneType' object has no attribute '__dict__'` → 改成先注册再 exec。
2. **`compile()` 会继承调用方的 `__future__` 标志** —— `tests/conftest.py` 自己带
   `from __future__ import annotations`，于是 exec 出来的 dataclass 注释**全变成字符串**
   （`field.type == 'LoggingConfig'` 而不是那个类），按类型递归当场废掉。
   加 `dont_inherit=True`。
   实测：同一段源码在带 / 不带 future 的上下文里 exec，`isinstance(field.type, str)`
   分别是 `True` / `False`。

> 也就是说：**「clone 到干净机器能跑」这条验收，如果不真的造一个干净副本去跑，
> 它就是一句空话** —— 本机永远看不见这两个坑。
