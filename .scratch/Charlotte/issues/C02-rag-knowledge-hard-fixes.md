# C02 · rag_knowledge 硬伤与口径修复

**Status:** done

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

**推论**：合并**要救的那些块**（<400 字符、未切分）此时**没有 `parent_title`** → 判据恒为假 → 它们永远进不了合并。

> **⚠️ 2026-09-30 实施期实测纠正 —— 上面这句原来写的是「合并永不触发」，那是错的。**
> 在 `hak180产品安全手册` 上量过（`_merge_chunk_content` 前后长度差）：
> **修复前也是 2 次合并、19 个 chunk；修复后同样 2 次合并、19 个 chunk**。
> 原因是：**纯 split 出来的块本来就带 `parent_title`**（`_split_chunk_content:278` 自己设了），
> 那 2 次合并正是发生在它们之间。补 `parent_title` 的时点确实错了，但它**不影响当前语料**——
> 它修的是「同一标题下，未切分的短板 + 长块切出的碎片相邻」这种本语料碰不到的情形。
>
> **连带作废的验收**：原写的「chunk 数下降、无 <400 字符孤儿块」**达不成也不该达成** ——
> 「未切分的块各有一个不同标题」是这套切分的设计（`_padding_chunks_metadata` 给它们的
> `parent_title` 就是自己的 title），所以相邻两块永远不同名、**本来就不会合并**；
> 而 <400 字符的块只要没有同名邻居，就只能是短板。这条判据是我按错模型写的。

**做法**：把 `_padding_chunks_metadata` 提前到合并之前（或让 `_split_document_by_title` 直接产出 `parent_title`）。二选一。验收改成人造输入：**构造同标题的两个相邻短板，断言它们真的被并成一块**（真实语料碰不到，就用最小实例钉）。

### 1.1b 切分器没有兜底 —— 超长块的真凶（**实施期新发现，已并入本票**）

`_split_chunk_content:271` 传给 `RecursiveCharacterTextSplitter` 的分隔符表是
`["\n\n", "\n", "。", "！", "？", "；", "，", " "]` —— **少了 LangChain 默认的最后一个 `""`**，
而那个 `""` 正是「实在切不动就按字符硬切」的兜底。

后果**可复现**：`chunk_size=574` 切 `"x" * 1400` 得到 **1 片 1400 字符**，`chunk_size` 形同虚设。
真实产物上：B530 有 793 / 944 字符的块、B730 有 796 / 944、**hak180 有一块 1403（还超了 `CHUNK_MAX_SIZE`=1000）**
—— 后者是一张被压成一行的 HTML 表格（`'## 产品中有害物质的名称及含量\n colspan="6">有害物质</td>...'`）。

**做法**：分隔符表末尾补 `""`。**这条才是「超长块」的真凶**，而它是**眼前这份语料正在中的招**
（而 1.1 那个路径当前语料一次都没碰上）。

### 1.2 单路召回为空 → 整个问答 500

- `app/rag/query/rrf_service.py:13-15`：`if not embedding_chunks or not hyde_embedding_chunks: raise ValueError`
- `app/rag/query/rerank_service.py:46-48`：对 `rrf_chunks` / `web_search_docs` / `rewritten_query` 同样处理
- **`app/rag/query/answer_service.py:66`（这一处写票时漏了，评审才补上）**：
  `_validate_data` 要求 `reranked_docs` 非空 → 三路全空时**作答节点仍然 raise**。

**后果**：Tavily 挂了、或知识库里没有该主体的内容 → **整条问答链 500，不是降级**。日志（`logs/app_20260901.log` 05:02）里有真实崩溃记录。评测里必须塞一条假 web 文档才能跑起来，就是这条硬伤的副产品。

> **⚠️ 只修前两处是不够的**：把 rrf 与 rerank 的 raise 拿掉之后，「检索不到」会一路走到
> 作答节点，在那里**继续 500** —— 我在第一版里就是这么漏过去的。**降级链要一直修到终点**。

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

### 2.4 题库的问句里写上主体名（2026-09-30 用户提出，并入本票）

**起因是实测出来的一个脆弱点**：`item_name` 集合的确认阈值是 0.70，而拿活着的
Milvus 量三种写法，最高分是 `0.7425 / 0.739 / 0.7255` —— **余量只有 0.04**。
问句里不点明主体时，这一层一抖就掉进 0.60~0.70 的「候选」区间，那条题**整条检索链
根本不跑**，四层指标全被这 0.04 绑架（题库 50 题里有 **35 题**的问句没写主体名）。

**做法：直接改题库 `artifacts/eval_cases.json` 的 `question` 字段**，把主体名写在
问句开头（按用户的示例，**不加分隔符**）：

```
改前: 如何安装全幅和半幅烫金膜盒？烫金膜松弛了怎么办？
改后: HAK180烫金机如何安装全幅和半幅烫金膜盒？烫金膜松弛了怎么办？
```

- 50 题里改了 **35 题**；**15 题原样不动**（B530/B730 那 14 题本来就带着 + `sm_01`）
- 写进问句的是**用户会打的写法**（`HAK180烫金机`），不是库里那串带下划线的
  （`HAK_180烫金机` —— 那是导入链路的 LLM 抽出来的）：`HAK_180烫金机` → `HAK180烫金机`、
  `RS_PRO_RS-12_数字万用表` → `RS-12数字万用表`
- 改动前的题库备份在 `D:\__WorkSpace__\Temp\rag_knowledge-c02\eval_cases.before.json`

**要认得代价**：问句里写了主体名之后，报告里的「平均主体命中率」只说明「明说的名字
能不能对上库里 item_name 的写法」，**不含**「从口语化提问里推断主体」——
这与 §2.1 刚做的事方向相反。这条边界写进了报告 `口径说明` 的第一条，
**没有另开一套测量装置**（一度加过一版「装置层拼 + 单开一趟主体识别」，按用户要求
全部撤掉了 —— 那套东西比问题本身复杂）。

---

## 三、口径与死代码

| # | 位置 | 问题 | 处置 |
|---|---|---|---|
| 3.1 | `README:175` | 说 web「取前 10 条」 | 代码是 **5 条**（`web_search_service.py:31`）→ 改 README |
| 3.2 | `README` §3.2 | 「三路并行召回 → RRF 融合」 | 实际是**两路 RRF + web 在精排层合并** → 改表述 |
| 3.3 | `web_search_service.py:51-79` | MCP 代码整段注释，但节点名仍叫 `_09_3_web_search_mcp`、`task_utils.py:48` 映射「网络搜索」 | 删注释代码 + 改名（或保留但把名字改对） |
| 3.4 | `escape_milvus_string_utils.py`、`normalize_sparse_vector.py`、`format_utils.py`、`path_util.py`、`settings_config.py`、`bailian_mcp_config.py` | **全项目零引用** | 逐个二选一：接上，或删掉 |
| 3.5 | `embedding_search_service.py:46` / `hyde_search_service.py:74` 的过滤表达式 | 裸 f-string 拼接，而 3.4 那个转义工具写了没用 | **接上**转义工具 → `build_in_expr` |

> **⚠️ 3.5 的定性被实测改了（2026-09-30）**。我原来写的理由是「Python list repr 用单引号,
> 而 Milvus 要双引号」—— **那是错的**。拿活着的 Milvus 逐条试过：**单引号双引号它都收**；
> 又拿带单引号 / 双引号 / 换行 / 反斜杠的值去跑原写法，**也没试出一个被拒的**
> （Python 的 list repr 恰好吐出了它认的形状）。
> **所以这条不是「修了一个已复现的 bug」，是防御性加固** —— 值来自文档标题（外部内容），
> 而 repr 挑引号是「碰巧能用」而不是契约；真出问题时的表现形式会是**静默匹配不到**
> （而不是报错），更难查。结论写进了 `build_in_expr` 的 docstring。
> 原写法的 `item_name_confirm_service` 那处过滤表达式（`expr=f"item_name in {item_names}"`）
> **本项目里不存在** —— 那是从别处串过来的。

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

> 原 1.1 那条（「chunk 数下降、无 <400 字符孤儿块」）按 §1.1 的实测纠正**作废** ——
> 见那里的说明：那是我按错的模型写的判据。

- [x] 1.1 补 `parent_title` 的时点前移；用**人造输入**钉住（同标题相邻短板真被并成一块 + 不同标题不被并）
- [x] 1.1b 分隔符表补 `""` 兜底；用**真实产物**钉住（最长 chunk 1403 → 625）
- [x] 1.2 单路为空 / 单路抛异常时**链路降级**而不是 500；有回归测试
      （**含作答节点那一站** —— 评审指出我第一版只修到精排, 三路全空时作答节点
      仍 `raise` → 依然 500；已补, 并有用例断言「没资料时不叫模型」）
- [x] 1.3 三处 `response[0]` 不再可能拿到 `None`（`hybrid_search` 改为拆包 + 永不返回 `None`）
      ＋ 补上规格点名过的 `get_milvus_client`：新增 `require_client()`，load 侧四处改用它
- [~] 1.4 **只做了一半**：合并时不再丢 `url` ✓、链接进了 prompt ✓、
      **且加了「用了就写 [参考来源] 区块」的指令** ✓；
      **但前端仍按 `textContent` 渲染，链接不可点** —— 那是前端特性（要动答案文本契约），
      不属本修复票，**显式记为未做**（见实施记录）
- [x] 1.5 除零守卫
- [x] 2.1 主体识别改为真实链路（`_08` 真跑 + 历史读写替空）；**报告数字待重跑**（见下）
- [x] 2.2 HyDE 的 patch 在 README §6.3 与报告 `口径说明` 字段里显著标注
- [x] 2.4 题库 50 题的问句里写上主体名（改 35 题，15 题本来就含，原样不动）；
      报告 `口径说明` 第一条写明这会让「平均主体命中率」的含义变窄
- [x] 三的口径逐条对齐；死代码逐个处置（`escape_milvus_string_utils` **接上**，其余四个**移走**）
- [x] 四的最小测试全绿（33 个用例）；`pytest` 不再因为脚本式测试而在收集期报错
- [x] 从干净目录 import：本项目 grep `from Lib` / `import Lib` / `from sentry_sdk` **零命中**

**没做完的一条**：**2.3 的重跑**。评测要真 Milvus + 真模型 + 真 LLM（约 10 分钟、真花钱），
按既定约定**交给用户手动跑**：
`cd project/rag_knowledge && python -m tests.test_rag_eval_tester`。
代码侧已就绪（口径说明写进报告、README §6.4 标注了「旧口径待重跑」）。

## 改了哪些文件

**改**
- `app/rag/load/split_service.py` —— `_padding_chunks_metadata` 提到合并之前；分隔符表补 `""`
- `app/rag/query/rrf_service.py` —— 单路为空不再 `raise`
- `app/rag/query/rerank_service.py` —— 只要求 `rewritten_query`；两路全空跳过打分；保留 `url`；除零守卫；顺手去掉 `abs`/`next` 两个内建名遮蔽
- `app/rag/query/answer_service.py` —— 联网来源把链接写进 prompt；**三路全空时返回固定答复而不是 raise**
- `app/prompts/answer_out.prompt` —— 加第 3 条：用了带链接的资料就写 `[参考来源]` 区块
- `app/infra/milvus.py` —— 新增 `require_client()`（取不到就报错）
- `app/rag/load/{index_service,item_name_service}.py` —— 四处改用 `require_client()`
- `app/shared/clients/milvus_utils.py` —— `hybrid_search` 拆包 + 永不返回 `None`
- `app/rag/query/{embedding_search,hyde_search,item_name_confirm}_service.py` —— 去掉 `response[0]`；前两个改用 `build_in_expr`
- `app/process/query/nodes/_09_1/_09_2/_09_3` —— 单路失败隔离；`_09_3` 改名并去掉 `_mcp`
- `app/process/query/agent/main_graph.py` · `app/shared/utils/task_utils.py` —— 跟着改名
- `app/rag_eval/runner.py` —— 主体识别真跑；不再注入联网占位；报告加 `口径说明`
- `app/rag_eval/artifacts/eval_cases.json` —— 35 条问句开头写上主体名
- `app/rag_eval/dataset.py` —— 删掉 `build_web_search_docs`
- `app/infra/config.py` · `app/shared/config/__init__.py` —— 去掉已删模块的聚合与导出
- `app/shared/utils/escape_milvus_string_utils.py` —— 新增 `build_in_expr`
- `README.md` —— 口径逐条对齐 + §6.3/§6.4 重写

**新增**
- `app/shared/runtime/route_isolation.py` · `pytest.ini`
- `tests/test_split_service.py` · `tests/test_route_degradation.py` · `tests/test_milvus_expr.py` · `tests/test_eval_metrics.py`

**移除**（移到 `D:\__WorkSpace__\Temp\rag_knowledge-c02\`，**没有真删**）
- `app/shared/utils/{normalize_sparse_vector,format_utils,path_util}.py`
- `app/shared/config/{settings_config,bailian_mcp_config}.py`
- `app/process/query/nodes/_09_3_web_search_mcp.py`（被 `_09_3_web_search.py` 取代）
- `tests/__init__.py`（它让 pytest 把**仓库根**插到 sys.path 最前 → `import app` 命中的是 Django 那个 `app/`）
- `web_search_service.py` 里那段从未启用的 MCP 注释代码

## 实施记录

### 2026-09-30

**四条「把 bug 放回去」的核对**（测试全绿不等于能抓 bug）：

| 放回什么 | 结果 |
|---|---|
| 补齐挪回合之后 | `test_split_service.py` 里 2 条失败（同标题相邻短板不再合并 / 进合并前 parent_title 缺失） |
| 去掉分隔符 `""` | 另 2 条失败（无分隔符长文本没被切开 / 超长行没被切开） |
| —— | 7 条里恰好 4 失败 3 通过，**反例照过**，证明不是重复用例 |

**1.1 的实测数据（这份文档上）**：

| | chunk 数 | <400 字符 | >600 | 最长 | 合并次数 |
|---|---|---|---|---|---|
| 修复前（落盘产物） | 19 | 11 | 1 | **1403** | 2 |
| 修复后（本次重跑） | 19 | 12 | 1 | **625** | 2 |

- **最长 1403 → 625** 是分隔符兜底的效果（那块被压成一行的 HTML 表格终于被切开了）。
- **合并次数两次都是 2、chunk 数都是 19** —— 补齐时点那个修复**在本语料上没有可观测影响**，
  它修的是当前语料碰不到的路径。这条如实记下，不当成「合并变好了」。
- `<400 字符` 从 11 涨到 12：切分边界变了之后的副产物，**不是回归**（短板只要没有同名邻居
  就只能是短板，见 §1.1 的纠正）。
- 那个 `>600` 的 625 **不是违规**：它是合并出来的块，上限是 `CHUNK_MAX_SIZE`(1000) 而非 `CHUNK_SIZE`(600)。

**顺带发现、本轮没动的**（记下来免得下一个人以为是漏了）：
`rrf_service._use_rrf_rank` 的权重写死 `(0.5, 0.5)`、`rerank_service` 的候选池仍依赖
`MILVUS_CHUNK_RRF_TOP_K` —— 属于调参范畴，归 C17 的消融实验。

### 代码评审（两轴）带回的东西

**Spec 轴捞到一条真漏项**（本票最重要的一条）：

| 发现 | 处置 |
|---|---|
| **1.2 只修到一半**：三路全空时作答节点仍 `raise` → 依然 500 | 已补（`NO_RELEVANT_DOC_ANSWER` + 固定答复分支 + 流式推送），并加用例断言「没资料时**不叫模型**」；把 raise 放回去验过会红 |
| 1.4 只做一半：url 进了 prompt，但没要求模型用它 | 已在 `answer_out.prompt` 加第 3 条要求（用了就写 `[参考来源]` 区块） |
| 1.3 点名未清：`get_milvus_client` 也返回 `None` | 新增 `infra_milvus.require_client()`；load 侧四处改用它（`runner.py` 那处本来就判空，不动） |
| `build_in_expr` 注释不准确 | 按实测改写（见 §三 的纠正框） |
| 「保留原始 score」这条没做 | **规格写错了** —— 那个 `score` 是占位，紧接着就被 reranker 覆盖；已在代码注释里写明，不改 |

**Standards 轴四条，全部已修**：

1. **注释用了中文顿号**（违反 §4.9「标点一律英文」）—— 核实过：全仓既有注释里带顿号的**只有 1 处**，
   所以确实是违规。我新写的 6 处已改；剩下 2 处是**既有代码**，按「精准修改」不动。
2. **两个测试声明了 `monkeypatch` 却没用** —— 删掉形参；顺手把另一处的「手工赋值 + try/finally」
   的 spy 换成 `monkeypatch.setattr`（自动还原，用例失败时也不会漏）。
3. **`pytest.ini` 注释里的「另外四处」变成了错数** —— 这**正是本项目在 L4 治过的「会漂的数字」**，
   我却又抄了一遍枚举。两个 `pytest.ini` 都改成不写数量。
4. **`rrf_service._validate_data` 名不副实**（我改完之后它不再校验了）→ 改名 `_collect_route_chunks`。

**判为「不违规、不改」的**：`isolate_route` 的 `except Exception`（有意为之，docstring 已写明代价）；
`milvus_utils.py` 沿用 Sphinx 风格 docstring（该文件全篇如此，同文件内一致优先）；
`_09_3_web_search.py` / `task_utils.py` / README 里「联网召回 / 网络搜索 / 联网」那三种叫法
（都是展示用短标签，3 处，不值当为它抽一层常量表）。
