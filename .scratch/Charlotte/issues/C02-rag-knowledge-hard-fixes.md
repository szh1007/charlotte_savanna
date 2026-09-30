# C02 · rag_knowledge 硬伤与口径修复

**Status:** todo

**Type:** fix

**Blocked by:** —

**上游:** `.scratch/Charlotte/PLAN.md` §2 组 0；2026-09-30 的代码审查（结论带 file:line）

## 为什么先做这一片

这个项目是「RAG 工程」这条经历的**主力**（链路全真、有 50 题评估与落盘报告、有自研细节）。但有三处会被当场问倒、一处会让「溯源」卖点作废。**分块合并失效与单路为空即 500 两处已读码复核过。**

---

## 一、会崩或会丢的

### 1.1 分块合并逻辑从不生效（真 bug）

`app/rag/load/split_service.py`

| 行 | 做什么 |
|---|---|
| `:21` | `_refine_split_and_merge_chunks(...)` —— **合并发生在这里（`:182` 调 `_merge_chunk_content`）** |
| `:24` | `_padding_chunks_metadata(chunks)` —— **`parent_title` 到这里才补上（`:294-295`）** |
| `:217` | 合并判据 `is_same_parent_title = bpt and npt and bpt == npt` |
| `:278` | 只有走过 `_split_chunk_content` 的长块才有 `parent_title` |

**推论**：合并**要救的那些块**（<400 字符、未切分）此时**没有 `parent_title`** → 判据恒为假 → **合并永不触发**。能用上合并的只有「两个都来自同一次长块切分」的窄情形。

**真实产物的证据**：`output/hak180产品安全手册/hak180产品安全手册_new.json` 共 19 个 chunk，其中 **11 个 < 400 字符**（最小 37、42、57 字符），全部 `part=1`。这些碎片直接进 rerank，是噪声来源。

**做法**：把 `_padding_chunks_metadata` 提前到合并之前（或让 `_split_document_by_title` 直接产出 `parent_title`）。二选一，**但要重新跑一遍真实文档并核对 chunk 数下降、无 <400 字符的孤儿块**。

### 1.2 单路召回为空 → 整个问答 500

- `app/rag/query/rrf_service.py:13-15`：`if not embedding_chunks or not hyde_embedding_chunks: raise ValueError`
- `app/rag/query/rerank_service.py:46-48`：对 `rrf_chunks` / `web_search_docs` / `rewritten_query` 同样处理

**后果**：Tavily 挂了、或知识库里没有该主体的内容 → **整条问答链 500，不是降级**。日志（`logs/app_20260901.log` 05:02）里有真实崩溃记录。评测里必须塞一条假 web 文档才能跑起来，就是这条硬伤的副产品。

**做法**（两件事一起做，缺一不可）：
1. **空值降级**：单路为空时按「这一路没贡献」处理，不抛错；全空才走「检索不到」的正常答复分支
2. **单路失败隔离**：某一路的异常不拖垮另外两路（`asyncio.gather(return_exceptions=True)` 或节点内自行吞成空列表 + error 日志）

### 1.3 Milvus 异常被吞成 `None`，下游 `response[0]` 崩

`app/shared/clients/milvus_utils.py:150-154` 捕获所有异常后 **返回 `None`**，而三处调用方直接 `response[0]`：

- `embedding_search_service.py:68`
- `hyde_search_service.py:96`
- `item_name_confirm_service.py:175`

→ Milvus 抖动变成 `TypeError: 'NoneType' object is not subscriptable`，**真实错误被掩盖**。`get_milvus_client`（`:26-48`）同样返回 `None` 而下游不判空。

**做法**：要么抛明确的 `MilvusSearchError`（调用方按 1.2 的降级处理），要么返回空列表并在日志里带上原始异常。**不能返回 `None` 让下游崩在一个不相干的类型错误上。**

### 1.4 web 结果的 URL 在精排合并时被丢掉

`app/rag/query/rerank_service.py:78` 把 web 结果的 `url` **硬编码成空串**，`:66/:76` 把原始 score 也置 0。

→ 答案里写「来源: 联网搜索」却**给不出链接**，「逐句可溯源」这个卖点在 web 那一路是空的。这是「溯源回答」这个说法最容易被戳的地方。

**做法**：合并时保留 `url` 与原始 score；回答侧把「来源」渲染成可点的链接。

### 1.5 除零边界

`app/rag/query/rerank_service.py:170`：`ratio = abs / current.get("score")` —— score 为 0 时 `ZeroDivisionError`。加一条守卫。

---

## 二、评测的诚实性（这一条最要紧）

### 2.1 主体识别那一层事实上没被测

`app/rag_eval/runner.py:264-271` —— 评测时**注入** `expected_item_names` 直接当检索主体，于是报告里的「平均主体命中率 1.0」是**构造出来的**，不代表系统能力。

**面试风险**：面试官追问「主体识别准不准」，答「那层评测里被 patch 了」就崩。

**做法**：**改成真跑** —— 让 `_08` 节点走真实的主体识别链路，把 `expected_item_names` 当 **ground truth 去算命中率**，而不是当输入塞进去。基线数字会变（这是好事：变成真的），重跑后更新 README §6.4 与本票的实施记录。

### 2.2 HyDE 的 patch 保留，但必须显著标注

`app/rag_eval/runner.py:276-279` 把 HyDE 输出 patch 成固定串。

**判断：这一条可以保留，但性质与 2.1 不同** —— 主体识别是**链路入口**，它被 patch 等于起点是假的；而 HyDE 只是**其中一路召回**，patch 它是为了评测可复现。

**做法**：保留 patch，但在报告与 README 里**明确写出**「HyDE 那一路为固定输出，其分数不代表真实 HyDE 效果」。可选：另跑一组「HyDE 真调用 × N 次取均值」作为附录（非必须）。

### 2.3 报告与 README 数字要一起更新

重跑之后 `artifacts/eval_report.json` 与 README §6.4 的表必须一致，并注明「2026-XX-XX 重跑，主体识别改为真实链路」。

---

## 三、口径与死代码

| # | 位置 | 问题 | 处置 |
|---|---|---|---|
| 3.1 | `README:175` | 说 web「取前 10 条」 | 代码是 **5 条**（`web_search_service.py:31`）→ 改 README |
| 3.2 | `README` §3.2 | 「三路并行召回 → RRF 融合」 | 实际是**两路 RRF + web 在精排层合并** → 改表述 |
| 3.3 | `web_search_service.py:51-79` | MCP 代码整段注释，但节点名仍叫 `_09_3_web_search_mcp`、`task_utils.py:48` 映射「网络搜索」 | 删注释代码 + 改名（或保留但把名字改对） |
| 3.4 | `escape_milvus_string_utils.py`、`normalize_sparse_vector.py`、`format_utils.py`、`path_util.py`、`settings_config.py`、`bailian_mcp_config.py` | **全项目零引用** | 逐个二选一：接上，或删掉 |
| 3.5 | `item_name_confirm_service.py` 的过滤表达式 | 裸 f-string 拼接，而 3.4 那个转义工具写了没用 | 二选一：接上转义工具，或如实说明「meta 是本地可信配置，不走转义」 |

---

## 四、最小回归测试（纯函数）

`tests/` 现在**是脚本不是测试** —— `test_load_graph.py:21` 在 import 期直接执行、还用不存在的 `test.pdf`，**作为 pytest 跑在收集期就炸**。

这一片不重建测试体系（那是 C17 的事），只为**本条修掉的东西**钉住：

- `split_service` 的合并顺序（构造同父标题的多个小块，断言真的被合并）
- `rrf_service` / `rerank_service` 的空值降级与单路失败隔离
- `rag_eval/metrics.py` 的 P/R/MRR/NDCG（纯函数，最好钉）

**顺带**：把现有四个「脚本式测试」从 pytest 的收集范围里摘出去（改名或加 `__test__ = False`），否则仓库里跑 `pytest` 是红的。

---

## 验收

- [ ] 1.1 合并真的生效：重跑 `hak180` 产物，chunk 数下降、无 <400 字符孤儿块（把前后对比记进实施记录）
- [ ] 1.2 单路为空 / 单路抛异常时**链路降级**而不是 500；有一条回归测试
- [ ] 1.3 三处 `response[0]` 不再可能拿到 `None`
- [ ] 1.4 答案里的联网来源带上可点链接
- [ ] 1.5 除零守卫
- [ ] 2.1 主体识别改为真实链路，报告里出现**真实**的命中率；README §6.4 同步更新并注明重跑日期
- [ ] 2.2 HyDE 的 patch 在 README 与报告里显著标注
- [ ] 三的口径逐条对齐；死代码逐个二选一处置
- [ ] 四的最小测试全绿；`pytest` 在仓库根不再因为脚本式测试而报错
- [ ] 从干净目录 clone 后能 import（`Lib` 类误导入在本项目也要 grep 一遍确认没有）

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
