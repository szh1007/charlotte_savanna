# C18 · rag_knowledge 并发优化 + CI + 测试补齐

**Status:** todo

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

---

## 一、CI（GitHub Actions）

- 一个 workflow：`ruff check` + `ruff format --check` + `pytest`（**只跑不需要外部服务的用例**）
- 需要真实 Milvus / Mongo / 模型的用例用 marker 排除（对齐仓库里 `CharAgent/pytest.ini` 的既有做法：`integration` / `pg` / `redis` 这些 marker + `addopts = -m "not ..."`）
- **仓库是公开的** —— CI 跑绿的那一块是个可见的质量信号

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

---

## 验收

- [ ] CI 跑通（lint + 不需要外部服务的测试），仓库首屏能看到绿标
- [ ] 新增用例覆盖 §二 列出的四处，且**全部不需要真实 Milvus / Mongo / 模型**
- [ ] 三路召回节点内部并发化，并发上限可配
- [ ] 查询侧 embedding 批量调用
- [ ] **README 里有延迟拆解表（P50 / P95，优化前后对照）**
- [ ] 无论优化成功还是省不动，都有**如实的结论与数字**
- [ ] 所有需要外部服务的用例都有 marker，且被 `addopts` 默认排除

## 开工前要定的

- CI 跑在 GitHub 的免费 runner 上（Ubuntu）—— 要确认既有测试在 Linux 上能跑（C01 修的 `Lib` 类误导入是前置）
- 并发上限取多少（嵌入服务是本机还是远程，决定这个数）

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
