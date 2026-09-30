# C08 · L5-a：知识源与摄取（知识表 → 内部端点 → 切分 → Milvus）

**Status:** todo

**Type:** feature

**Blocked by:** C05–C07（框架层先稳住，再往上叠）

**上游:** `CharApp/docs/PLAN.md` §5 的 L5 段；`CharAgent/docs/DESIGN.md` ⑧ 的 #45/#46/#47；`.scratch/Charlotte/PLAN.md` §6.2 的 L5-D1 / L5-D2 / L5-D3

## 现状（2026-09-30 查证，三条都是硬事实）

**① 语料是零。** `app/minimall/models.py` 的 10 个模型里没有任何知识库 / 文档 / 政策表；18 条迁移、12 个模板、静态资源、fixtures、DB 表逐处查过，**一处政策内容都没有**。唯一"像政策"的东西是**硬编码**的：`CharApp/minimall/prompt/system/v4.prompt:66-87` 的「术语」节与 `CharApp/minimall/tools.py:177-193` 的 `_REFUSAL_HINTS`。

**② `Product` 顶不上。** 本机 MySQL 只有 **5 条商品**，`description` 字段的内容**就是商品名重复一遍**（最长 13 字符「iPhone 17 Pro」）；没有 `detail` / `content` / 规格参数字段，`Category` 也没有描述字段。**没有任何可用于 RAG 的商品正文。**

**③ 可以搬的零件在 `project/charplot/rag/`。** 五个文件（`milvus.py` / `chunking.py` / `embeddings.py` / `rerank.py` / `query_rewrite.py`，共约 600 行）只依赖一个 config 模块，逻辑可整段复用。

> **所以本票的第一步不是写代码，是写语料** —— 政策文档此前没有任何载体，得先有内容。

---

## 一、Django 侧：知识表 + Admin + 内部端点

### 1.1 新建 `KnowledgeArticle`（`app/minimall/models.py`）

| 字段 | 说明 |
|---|---|
| `title` | 标题（如「退货与退款政策」） |
| `slug` | 稳定标识，进 chunk 主键用 |
| `category` | 短标签（政策 / 配送 / 售后 / 商品说明），给后续过滤留口 |
| `content` | **Markdown 正文**（`TextField`），管理员手写 |
| `is_active` | 下架即不进索引 |
| `sort_order` / `created_at` / `updated_at` | 惯例 |

按项目约定：字段带 `verbose_name`、加一条 migration、注册 `KnowledgeArticleAdmin`。

**为什么不做「知识库 + 文档」两张表**（charplot 的形）：L5 定位是**最小可演示**；政策是短文，一张表够；而「上传文件 → 解析 → 索引」那条链路在 `charplot` 与 `rag_knowledge` 里已经证明过两次，**第三次不加分**。

### 1.2 语料内容（这是本票的真实工作量）

至少覆盖 L5 验收要问的几类：**退货/退款政策 · 运费与配送 · 售后时效 · 支付与余额规则 · 订单状态含义**。写 5–8 篇，每篇几百字。

**边界（重要）**：**业务规则不进知识库**。「取消 vs 退款的区别」「库存以发货为界」这类是**系统的硬规则**，必须 100% 遵守 —— 它们留在 prompt 的「术语」节里，**不交给概率检索**。知识库放的是**可查的政策事实**（几天无理由、运费谁承担、超时怎么办）。这条边界要写进 ADR。

### 1.3 内部端点（`app/minimall/views_agent.py` + `urls_agent.py`）

`GET knowledge/articles/`，沿用 `X-Internal-Token`（fail closed）：

- 返回全部 `is_active` 文章的 `{slug, title, category, content, updated_at}`
- **直查 DB，不走 Redis 缓存** —— 沿用 L1a 定下的规则：agent 看到的必须是当下事实

---

## 二、CharApp 侧：新建 `CharApp/minimall/knowledge/` 包

| 文件 | 来源 | 改什么 |
|---|---|---|
| `milvus.py` | 搬 charplot | 换 config 引用；collection 名改成常量 `ca_knowledge`（单集合，不按 KB 分） |
| `embeddings.py` | 搬 charplot | 换 config；**保留 `resolve_local_model_path` 的「只认本地、拒绝静默下载」** |
| `chunking.py` | 搬 charplot | **改键**：charplot 按文件扩展名查参数表，这里知识来自 DB 行 → 改成按 `source_type`（当前只有 `article`，内容是 Markdown，直接用 md 的分隔符与 500/50） |
| `rerank.py` | 搬 charplot | 换 config；三级降级分支整段保留 |
| `query_rewrite.py` | 搬 charplot | **换模型调用**：charplot 绑自己的 `pipeline/llm.get_chat_model`，这里换成 `CharAgent` 的 `ChatModel`；降级语义（任何失败返回原 query）整段保留 |
| `retriever.py` | **重写** | 去掉 `kb_id` 与软删查询（本票阶段无软删）；**必须变成 async 或丢线程池** —— charplot 那份是同步 httpx + 同步模型推理，直接进你的 asyncio 服务会卡住事件循环（charplot README 自己记了这条） |
| `documents.py` | **新写** | DB 行 → 文档文本的组装（`title` + `category` + `content`），可参考 `project/menu/agent/milvus_sync.py:40-70`（但那是脚本级写法，别照抄结构） |

**chunk 主键沿用 charplot 的显式模式**：`f"{slug}-{chunk_index}"`（可反解来源，C10 的引用要用它做稳定标识）。**不要用 `auto_id`** —— `rag_knowledge` 用了 `auto_id=True`，代价是外部对不上 chunk 编号、只能回查。

---

## 三、索引脚本（CharApp 没有任务系统）

`python -m CharApp.minimall.knowledge.index`

- 取全部 `is_active` 文章 → 组装文档 → 切分 → embedding → **全量 drop + create + insert**
- **幂等**：重跑结果一致；改一篇文章重跑立刻生效
- 走 charplot 的 `ensure_collection` 形（drop + create），物理剔除下架文档

---

## 四、配置与模型预热

- 读 env 只许在 `CharApp/minimall/config.py` 一处翻译（既有约定）；新增 `CHARAPP_MILVUS_URL` / `CHARAPP_MODELSCOPE_ROOT` / `CHARAPP_EMBEDDING_MODEL` 等，同步 `.env.example`
- **启动预热（L5-D3）**：在 `CharApp/minimall/server.py` 的 lifespan 里后台触发 embedding 与 rerank 模型的加载 —— 共享桌面演示时「第一问卡 30 秒」是致命的
- **把内存占用记进 README**（bge-m3 约 4.3GB + reranker）—— 这是「我知道这个选择的代价」的证据

---

## 验收

- [ ] Admin 里写 3 篇政策 → 跑索引 → Milvus 里检索得到
- [ ] 改一篇 → 重跑索引 → 检索到的是新内容（幂等）
- [ ] 下架一篇 → 重跑 → 检索不到（drop + create 的物理剔除）
- [ ] 内部端点无 token → 403；错 token → 403；未配置 token → 全拒
- [ ] **agent 看到的必须是当下事实**：Admin 改完文章，不重启服务，重新索引后立刻生效（验证没走缓存）
- [ ] 干净目录 clone 后能跑：模型不存在时**报错并给出下载命令**，不静默从 HF 下载
- [ ] 服务启动后第一问的延迟与后续一致（预热生效）
- [ ] `CharAgent` / `CharApp` 的既有用例全绿

## 开工前要定的

- 索引脚本的触发方式：纯手工跑，还是挂在 Django 侧的某个动作上（本票建议**纯手工**，最小）
- 语料写几篇、写哪些（建议 5–8 篇，覆盖退货/运费/售后/支付/订单状态五类）

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
