# C18 · rag_knowledge 并发优化 + CI + 测试补齐

**Status:** done

**Type:** hardening

**Blocked by:** —

**上游:** `.scratch/Charlotte/PLAN.md` §2 组 C；C02（最小回归测试与脚本式测试的处置**已在那里做掉，本票不重复**）

## 现状（2026-09-30 查证）

| 事实 | 证据 |
|---|---|
| **`tests/` 是脚本不是测试** | `test_load_graph.py:21` 在 import 期直接执行、还用不存在的 `test.pdf` → 作为 pytest 跑**在收集期就炸**；四个文件都依赖真实 Milvus / LLM / Mongo。**没有 fixture、没有 mock、没有断言业务逻辑** |
| **无 CI** | 根仓库有 ruff + pre-commit，但只到 lint 层；没有任何 workflow |
| **rerank 极慢** | 日志实测**单次打分 9.1 秒**（`logs/app_20260901.log` 09:56:54），CPU、**单条串行、无 batch** |
| **Milvus 查询没有批量** | `milvus_utils.py` 批量插入是逐条 `insert`（非 bulk） |

> C02 已经做了「最小回归测试（metrics / RRF / 分块）」与「把脚本式测试从 pytest 收集范围摘出去」。**本票不重复那两件**，做的是**覆盖补齐 + CI + 性能**。

## ⚠️ 开工前读码核实：票面四处与代码不符（2026-10-06）

| 票面写的 | 代码里的实际 |
|---|---|
| §3.1「三路召回节点内部逐关键词串行 `aembed_query`」 | 这在 **rag_text2sql**（`_2_1_recall_column.py:47` / `_2_2_recall_metric.py:47` / `_2_3_recall_value.py:45`），**不在 rag_knowledge**。rag_knowledge 的三路召回是查询图的**三条并行边**，每一路只嵌 1 条文本，没有这样的循环 |
| §3.2「查询侧 embedding 没有批量」 | 同上，只对 rag_text2sql 成立；rag_knowledge 查询侧每路只嵌一条，加载侧本来就按 `EMBEDDING_BATCH_SIZE=5` 分批 |
| 「Milvus 批量插入是逐条 insert」 | **不成立**：`index_service._insert_chunks_data` 是一次 `insert(data=embeddings)` 全量插入；`item_name` 那条按设计一文档一条 |
| 「rerank 单条串行、无 batch」 | **不成立**：`FlagReranker.compute_score` 本来就吃 `pairs` 列表，已经是批量的。9 秒的代价在 CPU 推理本身（证据文件 `logs/app_20260901.log` 已被 gitignore 清掉，不可复核） |

**用户裁决（2026-10-06）**：并发优化**做在 rag_text2sql**（代码实际所在处）；rag_knowledge 侧做覆盖补齐 + 装置，真跑与题库扩容（C17）一起。**CI 不做**（见 §一）。

---

## 一、CI（GitHub Actions）—— **核实后不做**（2026-10-06 用户决定）

- 一个 workflow：`ruff check` + `ruff format --check` + `pytest`（**只跑不需要外部服务的用例**）
- 需要真实 Milvus / Mongo / 模型的用例用 marker 排除（对齐仓库里 `CharAgent/pytest.ini` 的既有做法：`integration` / `pg` / `redis` 这些 marker + `addopts = -m "not ..."`）
- **仓库是公开的** —— CI 跑绿的那一块是个可见的质量信号

> **决定与替代**（2026-10-06）：用户明确「不做 GitHub Actions，所有都在本地跑通再提交」。
> 本票**不做 workflow**，替代做法是**把本地跑绿这件事做实**：
> ① 需要外部服务的用例全部挂 `integration` 且被 `addopts` 默认排除（本票做了，见验收最后一条）；
> ② 两个子项目的全量用例 + `ruff check` + `ruff format --check` + `pre-commit` 本地跑通（本票做了，记录见实施记录）。
> **没有 workflow，就没有"首屏绿标"这个信号** —— 这条记在这里，别当成已经做了。

## 二、覆盖补齐（在 C02 的基础上）

补的是**能脱离外部服务跑的那部分**：

- `split_service` 的标题切分与「父_子」链拼接（纯函数部分）
- `item_name_confirm_service` 的阈值三分支（**mock 掉检索**）—— 这是链路入口，最该有测试
- `web_search_service` 的过滤与字段映射（mock Tavily 客户端）
- 加载链路的 `_05`–`_07` 三个节点（mock Milvus 客户端）—— 目标是把「chunk 数量与元数据字段」钉住，C02 改了分块合并之后尤其需要

**明确不做的**：真实模型质量的自动化断言（需要真模型、输出非确定，性价比不成立）—— 如实记进文档。

## 三、并发优化（要给出**前后数字**）

### 3.1 节点内串行 await → 并发

三路召回节点内部是**逐关键词串行**：每个关键词一次 `aembed_query` + 一次检索，`for` 循环里 `await`（`_09_1_search_embedding.py` 一带）。7 个关键词的列召回实测 ~1.4 秒。

→ 改成 `asyncio.gather` + **并发上限**（`Semaphore`，别把嵌入服务打挂）

### 3.2 embedding 批量化

现在逐条调用；`embedding_service` 已有 5 条一批的写法，查询侧没有 → 统一成批量接口。

### 3.3 rerank 批量化

`FlagReranker.compute_score` 本来就接受 `pairs` 列表 —— **它已经是批量的**，9 秒的代价在 CPU 推理本身。所以这里能省的只有：
- 减少送入打分对的条数（候选池从 20 降下来？—— 但那会动召回，**要先有 C17 的消融数据才能决定**）
- 换更小的 reranker 或用量化
- **诚实结论可能是「省不动」** —— 那就把「CPU 单机跑 bge-reranker 就是这个量级」写进文档，并给出「生产上该怎么做」（GPU / 独立服务 / 更小的模型）。**这比硬凑一个优化数字好。**

### 3.4 给数字

README 里加一节「延迟拆解」：每个节点各占多少毫秒、P50 / P95，优化前后对照。**TTFT 与总延迟分开报**（#66）。

> **落点与数字**（2026-10-06）：§3.1/§3.2 做在 **rag_text2sql**（三个召回节点），
> 表在它 README §3.4；**rag_knowledge 的延迟表待跑**（用户 2026-10-06：知识库/题库
> 正在重建，真跑与 C17 一起）。rag_knowledge README §3.3 写的是**方法与装置 + 逐条
> 「为什么这里没有可优化的串行点」**，没有留任何编造数字。
>
> **TTFT**：rag_knowledge 的作答节点才是流式（SSE `DELTA`），评测跑批器不跑它，
> 所以它的 TTFT 得靠一次真实的流式提问来量 —— 归到 C17 那次真跑一起（装置侧
> `app/rag_eval/latency.py` 只汇总节点耗时，TTFT 要么另写一段、要么从 SSE 时间戳算）。

---

## 验收

- [ ] CI 跑通（lint + 不需要外部服务的测试），仓库首屏能看到绿标
      → **核实后不做**（2026-10-06 用户决定）：不做 GitHub Actions，改成本地跑通再提交。
      替代做法与代价见 §一。**这一条不算达成，算"决定不做"。**
- [x] 新增用例覆盖 §二 列出的四处，且**全部不需要真实 Milvus / Mongo / 模型**
      （四块全覆盖：标题切分 4 + 主体阈值 12 + 联网过滤映射 4 + 加载三节点 6 = 26 条；
      另有本票新增的耗时汇总纯函数 7 条。全量 102 passed，不连任何外部服务）
- [x] 三路召回节点内部并发化，并发上限可配
      （**做在 rag_text2sql** —— 代码实际所在处；上限 `app_config.recall.concurrency`，默认 4）
- [x] 查询侧 embedding 批量调用
      （同上；`aembed_query` 逐条 → `aembed_documents` 一次批量）
- [x] **README 里有延迟拆解表（P50 / P95，优化前后对照）**
      （rag_text2sql README §3.4 有完整的前后对照；rag_knowledge README §3.3 是方法与装置，
      **数字待 C17 那次真跑**）
- [x] 无论优化成功还是省不动，都有**如实的结论与数字**
      （省下的是"逐条调用"那部分：列召回 P50 −37% / 指标召回 −46% / 取值召回 −55%；
      **省不动的是嵌入推理本身**，微基准给了数：4 条批量 180ms vs 逐条 504ms；
      rerank 那一处的结论同样是"批量化本来就已经做了"）
- [x] 所有需要外部服务的用例都有 marker，且被 `addopts` 默认排除
      （rag_knowledge：`pytest.ini` 加 `markers` + `addopts = -m "not integration"`，
      4 个手工脚本挂 `pytestmark = pytest.mark.integration` 并保留 `__test__ = False`）

## 开工前要定的

- CI 跑在 GitHub 的免费 runner 上（Ubuntu）—— 要确认既有测试在 Linux 上能跑（C01 修的 `Lib` 类误导入是前置）
  → **问题作废**：不做 CI（2026-10-06 用户决定）
- 并发上限取多少（嵌入服务是本机还是远程，决定这个数）
  → **答：默认 4，写在 `app_config.recall.concurrency`（有默认值，老配置文件缺这一节也能起）**。
  取 4 的理由是从实测反推的：检索本身只要 1~2ms 一次，瓶颈是嵌入推理（8 条批量 336ms），
  所以上限存在的意义是"别把连接池打满"而不是"跑满并发" —— 4 已经够，再往上只省几十毫秒排队。

## 改了哪些文件

**rag_text2sql（并发优化的落点）**

- 新增 `app/core/concurrency.py` —— `gather_limited`（保序 + Semaphore 上限，PEP 695 泛型）
- 新增 `app/eval/latency.py` —— 冻结词表重放 + 逐节点计时 + 召回 id 记录（两档口径 + CLI）
- 新增 `app/eval/fixtures/recall_keywords.json` —— 39 题的冻结关键词与 LLM 扩展结果
- 新增 `app/eval/reports/latency_recall_{before,after}.json` —— 前后对照的原始报告
  （node 档的两份**不入库**，理由见实施记录 §九）
- 新增 `tests/test_recall_concurrency.py` —— 11 条（批量/并发上限/上限可配/保序与去重）
- 改 `app/conf/app_config.py` —— 加 `RecallConfig.concurrency`（默认 4）
- 改 `app/agent/nodes/_2_1_recall_column.py` / `_2_2_recall_metric.py` / `_2_3_recall_value.py`
  —— 逐关键词串行 → 一次批量嵌入 + 带上限并发检索
- 改 `README.md` —— §3.4 延迟拆解（前后对照 + 证据 + 诚实的边界）+ §2 目录树

**rag_knowledge（覆盖补齐 + 装置）**

- 新增 `app/rag_eval/latency.py` —— 从日志汇总逐节点 P50/P95
- 新增 `tests/test_item_name_confirm_service.py`（12）/ `test_web_search_service.py`（4）
  / `test_load_nodes.py`（6）/ `test_eval_latency.py`（7）
- 改 `tests/test_split_service.py` —— 加 4 条「多级标题切分契约」
- 改 `tests/test_load_graph.py` / `test_query_graph.py` / `test_run_load_graph.py`
  / `test_rag_eval_tester.py` —— 挂 `pytestmark = pytest.mark.integration`
- 改 `pytest.ini` —— `markers` + `addopts = -m "not integration"`
- 改 `README.md` —— §3.3 延迟拆解（方法 + 装置 + 逐条「为什么这里没有可优化的串行点」）

## 实施记录

### 2026-10-06 · 一、先核实票面（读码 + 实测）

四处与代码不符，逐条记在文首的纠正表里。其中**两处是"补一个本来没有的优化"**：
rag_knowledge 既没有逐关键词的串行循环，`Milvus 逐条 insert` 也不存在。
用户裁决：并发做在 rag_text2sql，rag_knowledge 只补测试与装置；CI 不做。

### 二、测量装置（比数字更重要的一层）

`python -m app.eval.latency` 两个档：

| 档 | 关键词扩展 | 用来干什么 |
|---|---|---|
| `--mode recall` | 换成冻结结果（`--capture` 抓一次落盘） | A/B —— 前后两趟**输入完全一致**，差只可能来自代码 |
| `--mode node` | 真 LLM | 线上口径，含 LLM 抖动，只作量级参照 |

**为什么要冻词表**：关键词扩展是一次真 LLM 调用，同一个问题两次跑给出的词表不同，词表不同则检索次数不同，A/B 就不可比。冻结之后 39 题 × 3 节点 × 5 次重放的输入完全一致。

### 三、数字（recall 口径，P50 / P95 毫秒）

| 节点 | 前（第一趟 / 第二趟） | 后（第一趟 / 第二趟） | P50 变化（第二趟） |
|---|---|---|---|
| `recall_column` | 651/981 · 752/1172 | 427/661 · 473/772 | **−37%** |
| `recall_metric` | 1356/1751 · 1601/2063 | 766/995 · 870/1310 | **−46%** |
| `recall_value` | 16/29 · 22/36 | 8/13 · 10/15 | **−55%** |

- **两趟独立跑出来的比例一致**（−37/−46/−55 与 −34/−44/−49），逐题配对比值中位数 0.63/0.55/0.47，39 题里各只有 1 题变慢（噪声）。机器负载会漂（两趟绝对值差 15%~35%），所以看的是**同趟前后配对**。
- **A/B 是拿 `git stash` 回的原版代码跑的**：`git stash push` 三个节点文件 → 跑 BEFORE → `pop` → 跑 AFTER。前后两趟用的是**同一份装置**。

### 四、"结果没被换掉"的证据（这条比省了多少要紧）

- 117 组（39 题 × 3 节点）召回 id 的**集合全部一致**：0 组成员不同。
- 其中 30 组**顺序**不同 —— **不是这次改动的产物**：`merged_keywords = list(set(keywords + result))` 的迭代顺序由进程内字符串哈希决定。实证：同一份代码（都是改后版）换两个进程各跑一遍，40/117 组同样顺序不同。顺序只影响「同一字段被多个关键词召回时留哪一份」，属既有行为。
- **顺带发现、本票没动**：`list(set(...))` 让关键词顺序跨进程不稳定，于是"先到先得"的去重结果也跟着不稳定。要修得改成有序去重（比如 `dict.fromkeys`），那会**改变**哪些重复胜出的既有行为 —— 归调参/口径票（C17 那条线）更合适。

### 五、"省不动"的部分（同样要给数）

- **嵌入推理是地板**：微基准（TEI + bge-large-zh-v1.5 + CPU）—— `aembed_documents` 1 条 110ms / 4 条 180ms / 8 条 336ms；逐条 `aembed_query` 4 条 504ms。批量化省掉的正是逐条那 2/3 的往返；**再往上加并发的收益是几十毫秒的排队**（检索本身 1~2ms 一次，取值召回那一路只有检索，P50 才 10ms）。
- **rag_knowledge 的 rerank 不用优化**：`compute_score` 本来就吃 `pairs` 列表。CPU 单机跑 bge-reranker 是这个量级是**事实**（票面那次 9.1 秒的日志已被清掉、不可复核，所以只作为量级参考写进 README §3.3，不当数字用）。真要更快只有三条路：缩候选池（**等 C17 的消融数据**）、换小模型/量化、上 GPU。

### 六、验收执行记录（本地，无 CI）

| 项目 | 命令 | 结果 |
|---|---|---|
| rag_text2sql 全量 | `pytest -q` | **208 passed**（新增 11 条在内） |
| rag_knowledge 全量 | `pytest -q` | **102 passed**（新增 33 条在内） |
| ruff | `ruff check` + `ruff format --check` 两个子项目 | All checks passed / 180 files already formatted |
| pre-commit | `pre_commit run --files <本票改动的 14 个文件>` | 全 Passed（含**更旧的** ruff 0.12，无 UP038 类问题） |

### 七、判断为"不做"的

| 项 | 理由 |
|---|---|
| GitHub Actions workflow | 用户 2026-10-06 决定不做；代价（没有首屏绿标）写在 §一，别当成做了 |
| rag_knowledge 的延迟数字与 TTFT | 知识库/题库正在重建，用户明确"真跑与 C17 一起" —— README §3.3 只写方法与装置，**不留占位假数字** |
| 真实模型质量的**自动化断言** | 要真模型、输出非确定；放宽到"没崩"的断言没有信息量。**已如实记进文档**：rag_knowledge README §6.3 最后一条 |
| 把 `list(set(...))` 改成有序去重 | 会改既有去重行为，属口径改动（见四） |
| 给三个节点加"退化成串行"的开关 | 并发上限已可配（配 1 即串行），不必再加一层开关 |
| 两个子项目的 `percentile` 各留一份 | 它们分属两个独立子项目、没有共享包，为 8 行函数造一层跨项目依赖不划算（评审的"重复代码"建议，判断为不改） |

### 八、代码评审（两轴）带回的东西

**规格轴捞到一条真问题**（本票最值钱的一条）：

| 发现 | 处置 |
|---|---|
| **保序回归用例约半数种子下抓不住它要抓的坏实现**：用例的 docstring 说"先发的慢、后发的快"，但关键词顺序由 `list(set(...))` 决定，用例没把"慢"钉在**排前面**的那条上 —— 评审把 `gather_limited` 换成"按完成顺序返回"的坏实现实测：6 个哈希种子里有 2 个仍能通过 | 已改成 `merged = list(set(keywords))` 后按**真实顺序**分配延迟（排前面的那条慢）；复验：坏实现在 **6 个种子 × 2 条用例 = 12/12 全部被抓住**，不塞坏实现全 PASS |
| README 里那条派生统计（"逐题比值中位数 0.63/0.55/0.47，各 1 题变慢"）复算不出 | 是我混了两个口径（第 1 次单样本 vs 5 次中位数）。已统一成**每题 5 次的中位数再配对**：中位 **0.62 / 0.53 / 0.44**，**39 题里没有一题变慢**（最差 0.85），并把口径写进 README |
| README 的关键词个数区间（列 3~9 / 指标 8~16）是**未去重**的和，差一个端点 | 已改成去重后实际送检索的个数：列召回 3~8（中位 5）/ 指标召回 7~16（中位 11）/ 取值召回 2~10（中位 4）；jieba 那层 2~7（中位 4） |
| 微基准（110/180/336/504ms）无落盘文件、不可复核 | 已在 README §3.4 放一个可折叠的**复核代码块**（服务起着就能重跑），数字声称变成可复现的 |
| §二"明确不做：真实模型质量的自动化断言 —— 如实记进文档"没落实 | 已补进 rag_knowledge README §6.3 与 §七 |
| 工作区里 `app/rag_eval/artifacts/*` 三个跟踪文件被删（-5901 行）不在票据里 | **不是本票改的**：用户 2026-10-06 为题库重建有意删除（Milvus collection 一并清空），本票不动它们、也不提交它们 |

**规范轴**：两条硬项 —— ① 新增注释里的**中文顿号**违反 §4.9（`「」` 不算：既有业务代码里 4092 行在用，是仓库既定风格），已逐条改成 `,`（测试数据里的 `"## 一、简介"` 是数据，保留）；② 几处类型注解不准/缺失（`sink: list[str]` 实为 `list[list[str]]`、`lines` 无注解、裸泛型 `dict[tuple, list]`），已补齐。
另有一条判断题顺手改了：`_ids_of` 在 state 字段改名时会**静默返回空表**（报告就失去了"结果有没有被换掉"这条证据）→ 改成字段缺失时当场抛 `KeyError`。判为不改的：`percentile` 两项目各留一份（见 §七）。
**规范轴没抓出错的**：三处召回节点未抽公共件（判断为题面过小、抽了反而是 Middle Man）、`_runtime` 转发函数已顺手内联。

### 九、`node` 档报告被误读成「优化后更慢」（2026-10-06 用户读报告时提出）

**问题**：`latency_node_{before,after}.json` 摆在一起看，after 的 P95 / max 明显更差（列召回 P95 4349 → 12881、取值召回 9682 → 21599），而 recall 档明明是 after 更快 —— 看着像弄反了。

**不是弄反，是 node 档本来就不能做 A/B**：

- 报告的时间戳与代码状态对得上（before 跑在 `git stash` 回原版之后、after 跑在 `pop` 之后），
  且 recall 档**两趟独立对照**都是 after 更快 —— 标签反了的话这个不可能稳定成立。
- node 档 = 真 LLM 关键词扩展 + 召回段。**那一次 LLM 调用本身就要 1.5~5.1 秒**（2026-10-06 现测 6 次，中位 2.4 秒），
  上游偶发挂起时样本 20 秒以上（after 那趟就有 21616 / 23350ms 两个离群值，C16 的 60s 超时 + 2 次重试正是为它加的）。
  被改的召回段在 recall 档里是 10~1600ms 量级，**造不出 20 秒的样本**。
- node 两趟**没有交替**（先 before 后 after），机器负载漂移整段落到 after 那侧。
- 看 `min`（最不受抖动影响）：列召回 1956 → 1386、指标召回 2460 → 2033 —— 与 recall 档同向。

**处置**（用户 2026-10-06 定的：带 LLM 随机性的结果文件不留）：

1. **`latency_node_{before,after}.json` 从仓库移走**（挪到 `D:\__WorkSpace\__Temp\c18-rag-text2sql\removed-node-reports\`）。
   判据：它不能被拿来做任何对照，摆在那里只会误导下一个读它的人 —— 而"整节点几秒"这种话用文字说清就够，不需要一份会被误读的数字。
2. `--mode node` 这个档**保留**（想看线上量级时还能跑），但 README 写明**它落盘的报告不入库、不做前后对照**；
   harness 里加了 `MODE_NOTES`，**每一份报告自带一句「这一档能不能做 A/B」**，脱离 README 被单独读也不会误解。
3. README §3.4 相应改写：删掉所有来自 node 档的数字（含"召回段占整节点 38%→34%"那组），
   换成一句定性的话 —— 这次优化改善的是**稳定下限**，端到端秒数由 LLM 那次调用决定，不属本票。
   recall 档的表与结论一字未动。
