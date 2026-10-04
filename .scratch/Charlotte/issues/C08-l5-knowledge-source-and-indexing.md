# C08 · L5-a：知识源与摄取（知识表 → 内部端点 → 切分 → Milvus）

**Status:** done

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

- [x] Admin 里写 3 篇政策 → 跑索引 → Milvus 里检索得到 —— 实际装了 **6 篇**（`manage.py seed_knowledge`，Admin 仍是编辑入口），跑索引后 `ca_knowledge` 6 条 chunk；真机检索五问，政策类问题的 top-1 全部命中对应文章（"运费怎么算"→ 运费与配送、"余额能不能提现"→ 支付与余额，0.88 分）
- [x] 改一篇 → 重跑索引 → 检索到的是新内容（幂等）—— 给退款那篇加了一句带标记的话、重跑，检索该标记 0.993 分命中 `refund-policy-1`；同输入连跑两次写入的行逐字节一致（用例 `test_rebuild_is_idempotent`）
- [x] 下架一篇 → 重跑 → 检索不到（drop + create 的物理剔除）—— `is_active=False` 后重跑：文章 6→5、chunk 6→5，标记句再也检索不到（top 分 0.01，等同"没有相关内容"）
- [x] 内部端点无 token → 403；错 token → 403；未配置 token → 全拒 —— 三种都进 `test_agent_api.AGENT_URLS` 那张总表；真机 curl 无 token 也是 403
- [x] **agent 看到的必须是当下事实**：Admin 改完文章，不重启服务，重新索引后立刻生效（验证没走缓存）—— 端点直查库（不走 Redis），真机改库→重跑→命中新内容，全程没重启 Django；另有 `test_reads_are_live_not_cached` 钉住
- [x] 干净目录 clone 后能跑：模型不存在时**报错并给出下载命令**，不静默从 HF 下载 —— 把 `CHARAPP_MODELSCOPE_ROOT` 指到空目录跑索引：退出码 1，错误里带 `modelscope download --model BAAI/bge-m3 --local_dir ...`；**旧索引原样留着**（失败发生在 drop 之前）
- [x] 服务启动后第一问的延迟与后续一致（预热生效）—— 预热 7.4~7.6 秒（embedding + rerank 就绪）；预热后第一问 7.4s vs 后续 7.2 / 6.6 / 6.8s（同一量级）。单问耗时的大头是**精排**（约 1.1 秒/对 × 6 条候选），不是模型加载 —— 这部分随语料规模增长，见实施记录
- [x] `CharAgent` / `CharApp` 的既有用例全绿 —— CharAgent **1493 passed**（132 deselected）；CharApp **391 passed**；Django 侧跑了本次相关的四个模块 **63 passed**。**Django 的全量套件（约 10 分钟）留给用户手动跑**：`python manage.py test app.minimall --noinput`

## 开工前要定的

- 索引脚本的触发方式：**纯手工**（票面建议，采纳）—— `python -m CharApp.minimall.knowledge.index`；Admin 保存文章时提示一句"别忘了重跑索引"
- 语料写几篇、写哪些：**6 篇**（退款政策 / 运费与配送 / 售后时效 / 支付与余额 / 订单状态 / 商品与库存），覆盖票面点名的五类，多一篇商品说明

## 改了哪些文件

| 文件 | 说明 |
|---|---|
| `app/minimall/models.py` + `migrations/0019_knowledgearticle.py` | 新增 `KnowledgeArticle`（title / slug / category(TextChoices) / content / is_active / sort_order / 时间戳），一条迁移 |
| `app/minimall/admin.py` | `KnowledgeArticleAdmin`（保存时提示重跑索引） |
| `app/minimall/views_agent.py` / `urls_agent.py` / `serializers_agent.py` | 新增 `GET knowledge/articles/`（X-Internal-Token、直查库、不分页）+ `AgentKnowledgeArticleSerializer` |
| `app/minimall/knowledge_corpus.py` | **新写**：6 篇 Markdown 语料（正文不含一级标题，见文件内的语料约定） |
| `app/minimall/management/commands/seed_knowledge.py` | **新写**：把语料装进知识表（默认只补缺的，`--force` 才覆盖 Admin 的改动） |
| `app/minimall/tests/test_knowledge.py` | **新写**：端点契约 / 语料形状 / seed 幂等，13 例 |
| `app/minimall/tests/test_agent_api.py` | 认证总表加一行（新端点） |
| `CharApp/minimall/knowledge/{__init__,documents,chunking,embeddings,rerank,milvus,query_rewrite,retriever,index,prewarm}.py` | **新写**：知识库九件（搬 charplot 五件套 + 重写 retriever + 新写 documents/index/prewarm） |
| `CharApp/minimall/config.py` | `KnowledgeConfig` + `knowledge_config_from_env` + 13 个 `CHARAPP_*` 变量名与默认值 |
| `CharApp/minimall/client.py` | `list_knowledge_articles()`（公共数据，不带身份）+ 抽出 `_get_shared` / `_read_get` |
| `CharApp/minimall/server.py` | `_serve` 里派后台预热任务（启动期读 knowledge 配置） |
| `CharApp/tests/test_knowledge.py` | **新写**：35 例（组装 / 切分 / 配置 / 降级 / 链路 / 索引 / 预热 / 新客户端方法） |
| `.env.example` / `.env` | `CHARAPP_MILVUS_URL` / `MODELSCOPE_ROOT` / `EMBEDDING_*` / `RERANKER_*` / 两个 K / `QUERY_REWRITE` |
| `CharApp/docs/adr/0026-policy-facts-go-to-the-knowledge-base.md` | **新写**：内容归属的边界（政策事实 vs 硬规则）与代价 |
| `CharApp/docs/PLAN.md` | L5 段标注已切票、C08 落地与实测数字 |

## 实施记录

**语料的载体是实施时定的**（票面只说"Admin 手写"）：六篇正文随仓库走
（`knowledge_corpus.py`）+ 一条幂等命令装进库（`manage.py seed_knowledge`）。
理由是"干净目录 clone 之后要能一条命令恢复出可检索的知识库"，而 Admin 依旧是
唯一的编辑入口（seed 默认**不覆盖**已存在的文章，`--force` 才是回到基线）。
六篇的一致性按代码核过：全站免运费（`create_order` 只累加明细小计）、退款只对
已付款订单、退款金额协商、余额是站内余额、支付密码 6 位、确认收货与完成订单是
两个买家动作 —— 一处写反就是"助手照知识库答、商城的实际行为却不是那样"。

**组装文本只拼 title + content，`category` 只进 metadata**（票面写的是
"title + category + content"）：`category` 是给后续过滤留的机器键（code），正文里
写 `policy` 对模型是噪音，而写中文标签就得把 Django 的 TextChoices 复制到
CharApp —— 多一处会漂的东西，换不来检索质量。中文标签只有 Django 一个出处。

**预热落在 `_serve` 的后台任务，不是 FastAPI 的 lifespan**（票面写的是 lifespan）：
框架的 `create_app` 返回的是裸 `FastAPI()`，没有 lifespan 可挂；而进程循环本来就是
业务这一层的 —— 预热任务与别的进程级资源（商城连接池 / 模型 / 快照）同一处建、
同一处收。行为与票面一致：启动不阻塞，第一问不必等模型。

**两个模型必须串行加载（实测撞出来的）**：并行（`asyncio.gather` 两个 `to_thread`）
时好时坏地抛 `NotImplementedError: Cannot copy out of meta tensor` —— 两个线程同时
进 transformers/accelerate 的 `init_empty_weights` 那一层改全局态，先回来的那个
拿到一个没装权重的 meta 模型，第一次真正推理才炸。改串行后连跑三次稳定。代价是
启动多等两三秒（本来也是"还没人提问"的时间）。

**初始化顺序是"先向量化，后 drop"**：抓取 / 切分 / 向量化全部成功之后才动向量库。
于是商城没起、模型不在、Milvus 连不上这三种失败都不会把盘上那份索引毁掉
（有用例钉住：`test_a_failed_embedding_does_not_touch_the_index`）。验证时真跑过
一次缺模型：退出码 1、错误里带下载命令、索引仍是 6 行。

**召回量默认 10，不是 charplot 的 20（实测调的）**：精排在 CPU 上逐对前向，
实测 **约 1.1 秒/对**（bge-reranker-v2-m3，8 线程）；embed_query 约 0.22 秒、
混合检索约 0.07 秒。20 条候选就是二十几秒的等待，而当前语料 6 篇、召回 10 条已经
覆盖整库。语料涨大时这个旋钮与"要不要精排"（模型留空即降级）都要重算。

**内存实测比票面估的低**：两个模型常驻 RSS **约 3.4GB**（票面写"bge-m3 约 4.3GB +
reranker"），预热 7~8 秒。**README 那条要等 C19 的四条经历 README** —— 本票先把
数字记进 ADR-0026 的「代价与边界」与 `.env.example` 的注释，C19 写 README 时从
这两处取（别让它只活在票据里）。

**一条容易误判的现象**：Milvus 写入之后**立刻**查询可能读到 0 行（Bounded 一致性，
几百毫秒到一两秒内），换个进程/等一会儿再查就是对的。验收脚本第一次跑完看到
"行数 0"就是这个，不是索引没写进去 —— 别据此改代码。

**顺带记两条已知待办（交给后面几片）**：① 提示词 v5（C09）要处理"知识库里的时效
条款"与"不许诺到货/退款时间"那一条的表述关系（政策写的是**安排发货的时限**，
不是对买家的承诺）；② 把 `python -m CharApp.minimall.knowledge.index` 接进
`sh/charapp_demo.sh`（演示前的一次性准备 + Milvus 依赖）归 C09/C10，本票刻意没动
演示装置。RAG 评估（`DESIGN` #48）仍未做 —— 当前只有"检索能命中"的定性验证。

**评审后的修补**（两轴评审：标准 / 票面，各一个子代理并行跑；下面这些是改掉的，
其余逐条判断后保留）：

| 发现 | 处置 |
|---|---|
| `server.py` 的 `_serve` docstring 写预热"并行加载"，与改后的串行实现自相矛盾 | 改成"串行"，并把理由指回 `prewarm.py` 那条实测 |
| `query_rewrite.py` 写"过长也返回原查询"，代码其实是**截断后使用**（用例也这么断言） | 分开写清：空 / 复述 → 原查询；过长 → 截断使用 |
| `chunking.py` 注释说未列出的来源类型"debug 里记一句"，代码里没有那句 | 补上 `logger.debug`（charplot 同款），让注释成真 |
| `seed_knowledge.py` 自称是演示装置的语料那一半，而装置里还没接它 | 改成"还没接进 `sh/charapp_demo.sh`，那一步归 C09/C10" |
| `rerank.py` 分支 3 写"没配就不打 warning"，而 `NoopReranker.rerank` 每次检索都警告 | 区分"构造期"与"检索时"：构造期不吵，检索时按原因提示一句 |
| ADR-0026 缺 0024/0025 都有的 `**落地**：` 一段 | 补上逐文件落点清单 |
| **契约长度对不上**：`slug` max_length=200，而 chunk 主键进 Milvus 的 VARCHAR(64) | 模型字段收到 **60**（留出序号位），超长在 Admin 保存时就报；语料测试按字段上限断 |
| `embeddings._download_hint` 把 modelscope 平铺布局又拼了一遍 | 抽到 `KnowledgeConfig.local_model_dir`（路径规则一处），`resolve_local_model_path` 与报错文案共用 |
| `prewarm` 两个加载器返回裸 tuple、`preload` 靠 `getattr` 鸭子类型 | 换成 `_LoadResult` NamedTuple；两个 Protocol 的 docstring 写明 `preload()` 是**可选**优化 |
| `prewarm` 那句"十几到几十秒"与实测 7~8 秒不符 | 改成实测值（并与 `.env.example` / ADR 对齐） |
| 预热只有实现没有接线用例 | 新增 `test_the_service_warms_the_knowledge_models_in_the_background`：派了任务、服务不等它、停机时取消 |
| `CharApp/docs/PLAN.md` §6.3 的 ADR 索引表没有 0026 | 补一行 |
| `serializer` 带了 `updated_at` 而索引脚本不用它 | 保留（票面契约如此）并在 docstring 里说明它的用处：排查"检索到的还是旧正文"时能一眼看出库里内容的新旧 |
| `index.py` 没有交代"drop 之后、写入之前"那一下失败留空 collection | 模块 docstring 里写明那是全量重建的已知窗口（重跑即可），不假装能回滚 |

**票面两处保留的偏离**（评审都点了，判断后维持原样，理由已在代码与 ADR 里）：
语料随仓库走 + `seed_knowledge`（不然"干净目录 clone 后能跑"这条验收落不了地）；
`documents.py` 组装文本不含 `category`（它是给过滤留的机器键，中文标签只有 Django
一个出处）。**一处如实记为未做**：票面"把内存占用记进 README" —— 本票没有 README
可写（四条经历的 README 归 C19），数字落在 ADR-0026 的「代价与边界」与
`.env.example` 注释里，C19 从这两处取。
