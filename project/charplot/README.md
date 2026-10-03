# CharPlot — AI 闯关学习网站（双后端微服务）

> 输入想学的**任何知识**（一句话 / 一段话 / 文档 / 网页链接 / 管理员预建知识库）→ AI 联网获取知识 → 解构成技能树图谱 → 渐进生成闯关题目 → 游戏化答题（Duolingo 式：心动值 / 连胜 / XP）→ 通关复盘报告可分享。
>
> 架构按**真实产品**设计（账号体系 / 分享页 / 后台分析 Dashboard 齐全），付费商业机制（连胜冻结卡等）一律降级为学习币兑换的轻量化实现。

---

## 1. 项目概览

| 能力 | 说明 |
|------|------|
| 知识获取 | 统一知识管道：输入归一化解析（txt/md/html/pdf/docx/pptx/网页链接）→ LLM 主内容分析 → 联网搜索增强 → 图谱解构 |
| 知识图谱 | 章节 → 知识点（带前置依赖边），技能树 / 关卡 / 间隔复习全部锚定图谱 |
| 闯关学习 | 关卡按知识点粒度渐进生成（3 分钟/关），选择 / 判断 / 填空三题型，5 心动值安全失败机制，断点续答，通关结算 |
| 间隔复习 | 新关生成时混入 Top 20% 历史易错题（易错分 × 时间衰减规则调度，无 LLM 参与） |
| 复盘报告 | 通关后生成知识总结，slug 公开只读分享页（无登录可看 + OG 卡片） |
| 知识库（RAG） | 管理员预建知识库（文档上传 / 软删 / 全量重建），主题卡片直达 Journey；企业级检索链路：解析 → 按类型调优切分 → bge-m3 embedding → Milvus 混合检索 → query rewrite → rerank 精排（本地模型缺失时降级不精排，`/ai/health` 暴露真实状态）→ 带引用生成 |
| 游戏化 | XP / 等级 / 连胜（冻结卡降级为学习币兑换）/ 心动值 / 学习币，全部规则层后端强制 |
| 后台分析 | 掌握度矩阵 / 活动统计 / 易错清单（事实聚合）+ LLM 文字版状态总结 |
| 幻觉防护 | 三层：题目讲解只基于检索片段 + 来源引用展示 + 「题目有问题」反馈标记 |

### 技术栈

| 组件 | 技术 |
|------|------|
| 状态与数据端 | Django 6.0 + DRF + MySQL + Redis（`app/charplot`, 主项目 8000 端口） |
| AI 能力端 | FastAPI 0.139（端口 8004） |
| 三件套分工 | LangGraph = 知识管道编排（`pipeline/`）· DeepAgents = 检索 subagent（`agents/`）· LangChain = RAG 组件（`rag/`） |
| LLM | DeepSeek（`init_chat_model` 接入, `CHARPLOT_DEEPSEEK_MODEL_NAME`） |
| Embedding / Rerank | 本地 bge-m3（稠密+稀疏一次出, 1024 维）/ bge-reranker-v2-m3, modelscope 预下载到本地目录（见 §5.3）, 加载前校验存在, 缺失不自动下载 |
| 向量库 | Milvus（KB 级 collection, 全量重建 + 软删 filter） |
| 任务系统 | FastAPI 异步任务 + Redis 状态 + SSE 进度推送（Last-Event-ID 断线续推） |
| 前端 | Vue 3 + Vite + TypeScript + Element Plus（B 站粉动漫主题）+ vue-flow 技能树（端口 9004） |

### 端口规划

| 端 | 端口 | 启动 |
|----|------|------|
| Django（业务/账号/分享页） | 8000 | `python manage.py runserver`（项目根 .venv） |
| FastAPI（AI 能力） | 8004 | 见 §5.4 |
| Vue 前端（dev server） | 9004 | `cd frontend && npm run dev`（/api、/r 代理 8000, /ai 代理 8004） |

---

## 2. 架构总览

**双后端微服务**：Django = 状态与数据（账号体系 / 学习数据 / 闯关交互规则 / 知识库元数据 / Dashboard / 分享页）；FastAPI = AI 能力（知识管道 / RAG 全链路 / 题目生成 / 任务系统）。闯关交互归 Django（判分与游戏化是纯规则 + 预生成讲解，**LLM 不参与答题路径**）；RAG 全链路归 FastAPI，Django 只存知识库元数据。

```
┌────────────── Vue 3 前端 (frontend/, 9004) ──────────────┐
│  Element Plus 动漫主题 + vue-flow 技能树 + 答题动画组件       │
└──────┬────────────────────────┬─────────────────────────┘
       │ HTTP /api /r           │ HTTP /ai + SSE
┌──────▼──────────────┐  ┌──────▼─────────────────────────┐
│ Django (app/charplot)│  │ FastAPI (project/charplot)      │
│ 账号/Profile/游戏化  │◄─┤ 知识管道 (LangGraph 4 阶段)      │
│ Journey/图谱/关卡    │  │ 题目生成 + 间隔复习混入           │
│ 判分/心动值/XP/连胜  │  │ RAG 索引/混合检索/rerank          │
│ 知识库元数据/软删    │  │ 任务系统 (Redis + SSE)            │
│ Dashboard/分享页     │  │ LLM 状态总结                     │
└──────┬──────────────┘  └──────┬─────────────────────────┘
       └──────── 共享 ──────────┘
        MySQL (schema 归 Django) / Redis /4 (任务) / Milvus
```

- **服务间通信**：FastAPI 调 Django 内部端点一律 `X-Internal-Token`（`CHARPLOT_INTERNAL_TOKEN`, 两端 .env 同值, 未配置 fail closed）；**不存在 Django → FastAPI 反向调用**（索引/出题触发由前端直调 `/ai/*`）
- **共享存储**：MySQL schema 归 Django ORM 管理；FastAPI 读题/写学习数据一律经 Django 内部端点，不直连库

### AI 能力全景（LLM 参与的五个流程）

| 流程 | 编排 | 状态 |
|------|------|------|
| A. 知识管道: 解析(无 LLM) → 主内容分析 → 联网搜索增强 → 图谱解构 | LangGraph StateGraph 编排, 检索环节套 DeepAgents subagent | ✅ |
| B. RAG: 索引(批处理) → 检索(rewrite → 混合 → rerank → Top-K) | LangChain 管线式, 被动服务 | ✅ |
| C. 题目生成: 知识点 + 检索片段 → 题目 JSON(讲解+来源引用) | LLM 单轮生成 + 结构校验 (非 subagent) | ✅ |
| D. 闯关答题: 判分/心动值/间隔复习混入 | 纯规则, **无 LLM** | ✅ |
| E. LLM 状态总结: 统计聚合 → 文字报告 | 裸 LLM 调用 | ✅ |

> 关键设计：**RAG 只返回片段不生成答案**——生成动作（图谱/题目/讲解）全在 A/C 的 LLM 环节，「生成依据」与「生成动作」拆开，实现幻觉防护。

---

## 3. 目录结构

```
project/charplot/                    # FastAPI 侧（AI 能力）
├── api/                             # server.py (7 个 /ai/* 端点) / tasks.py (任务系统+SSE)
│   │                               #   django_client.py (13 个内部端点客户端) / schemas / config
├── pipeline/                        # 知识管道: sources/ (检索源抽象: 网络/Context7/文档/知识库)
│   │                               #   + stages/ (parse/analyze/search/deconstruct) + graph.py
│   │                               #   + contract/types/llm/questions/parsers/json_utils
├── agents/                          # DeepAgents 检索 subagent + @tool 源封装
│   │                               #   (出题/解构为裸 LLM 调用, 不在本目录)
├── rag/                             # 索引(chunking/embeddings/milvus) + 检索(retriever/
│   │                               #   query_rewrite/rerank)
├── prompt/                          # prompt 配置(analyze/search/deconstruct/questions/status_summary)
├── frontend/                        # Vue 3 + Vite + TS (11 views: Home/闯关地图/答题/复盘/
│   │                               #   Profile/Dashboard/KBManage/Login…)
├── tests/                           # FastAPI 侧 11 个测试文件 (Redis /15 隔离 + Fake LLM, 不触网)
├── .env / .env.example              # CHARPLOT_* 前缀独立配置 (不提交 / 模板可提交)
└── pytest.ini                       # pythonpath=../.. → 以 project.charplot.* 包导入

app/charplot/                        # Django 侧（状态与数据, 主项目内）
├── models.py                        # 12 表: profile / user_event / journey / chapter /
│   │                               #   knowledge_point / level / question / attempt /
│   │                               #   review_report / question_flag / knowledge_base(+document)
├── services.py                      # 规则层: 判分/心动值/重开/XP/连胜冻结/易错分/间隔复习/
│   │                               #   学习币/validate_graph/幂等抢占
├── views_api.py / views_html.py     # API + 公开分享页 (CBV)
├── dashboard.py                     # 掌握度/活动/易错点聚合
├── urls_api.py / urls_html.py / serializers.py / permissions.py / signals.py
└── migrations/ (9) / tests/ (277 用例)
```

---

## 4. 核心业务链路

### 4.1 用户旅程主线（材料 / 主题输入）

```
输入(文本/链接/文件/知识库)
  → POST /ai/pipeline (FastAPI, LangGraph: parse → analyze → search → deconstruct)
  → SSE 进度 15/35/60/90/100 → 图谱 JSON → POST Django /journeys/{id}/graph/ 落库
  → 闯关地图(技能树渲染, 前置依赖边 + 点亮) → 关卡懒生成
  → POST /ai/levels/generate → claim(幂等/陈旧重抢) → 生成题目
      (新关题目 + 间隔复习混入 Top 20% 历史易错题, 复习题答案仅内部传递)
  → POST /journeys/{id}/level-generation/questions/ 落库 (有 Attempt 则 update-in-place 保历史)
  → 答题: POST /levels/{id}/answer (规则判分 + 心动值 -1 + XP + 讲解 + 来源引用)
  → 通关结算 (XP/学习币/连胜/节点点亮) → 复盘报告生成 + /r/{slug} 公开分享
```

### 4.2 知识库链路（管理员 → 学习者）

```
管理员: 创建 KB → 上传文档(格式白名单, all-or-nothing) → POST /ai/kb/index (真实解析切分入库)
  → SSE 进度 parsing→chunking/embedding→indexing → kb → ready
  → 学习者: 主题卡片页 → 点击直达 → 知识库驱动 Journey (RAG 两轮解构, 主内容 = Milvus)
软删: DELETE 文档 → Django is_deleted + 检索实时排除 (valid==true and doc_id not in 软删集) → 立即不命中
```

### 4.3 任务系统语义

| 任务类型 | 阶段进度 | 失败处理 |
|----------|---------|---------|
| pipeline | 15 / 35 / 60 / 90 / 100 | journey → failed, 可重试（重新 POST） |
| level-generation | 10 / 60 / 90 / 100 | 关卡 questions_status=failed, 可重试；generating 超 10 分钟可重新抢占 |
| kb-index | parsing 15 → 逐文档 40→85 → 90 → 100 | kb → failed, 可重试；indexing 超 10 分钟可重新抢占 |

SSE 事件统一 `pipeline-progress`，每帧带递增 `id`，断线重连按 `Last-Event-ID` 增量续推。失败语义：落库写自动重试 1 次（transient 5xx/连接错误）。

任务不持久化（FastAPI 重启丢失执行体，Redis 里的任务 hash 仍在），**孤儿任务回收**兜底：订阅时若「无新事件 + 执行体不在进程注册表 + 状态仍是 running」，判定为服务重启遗留 → 写终止 error 事件（订阅方按既有失败分支恢复）+ 经内部端点把实体推回失败态（关卡/知识库/旅程 `failed`），前端点「重试」即可真跑，不必等陈旧锁到期。（判据为单进程部署前提，本项目不启用多 worker。）

---

## 5. 快速开始

### 5.1 前置服务

| 服务 | 用途 | 说明 |
|------|------|------|
| MySQL | Django 主库 | 主项目 settings.dev, 建库后 `python manage.py migrate` |
| Redis | Django 缓存 /0 + 任务状态 /4 | 与主项目同一实例, CharPlot 用 `CHARPLOT_REDIS_URL` 指定独立库 |
| Milvus | 知识库向量 | 仅知识库链路需要 (与 deep_search 共用实例) |
| Django 主应用 | app/charplot | 根项目已注册, `python manage.py runserver` (8000) |

### 5.2 环境变量

```bash
cd project/charplot
cp .env.example .env        # 填入 DeepSeek / Tavily / Internal-Token
```

> 关键项：`CHARPLOT_INTERNAL_TOKEN` 必须与 Django 侧（主项目 .env 或 settings 读取处）配置为**同一值**，否则内部端点 fail closed；`CHARPLOT_DEEPSEEK_*` 未配置时管道不可用；`CHARPLOT_TAVILY_API_KEY` 未配置时网络检索源降级跳过（Context7/文档源不受影响）。

### 5.3 本地模型准备（仅 RAG 链路需要, 项目仅用 2 个本地模型）

模型加载前**先校验本地目录存在**, 缺失不会自动下载 —— 需先下载到 modelscope 目录（默认根 `CHARPLOT_MODELSCOPE_ROOT`, 本项目 `D:/__WorkSpace__/modelscope`, 平铺结构 `{root}/models/{org}/{name}`）:

```bash
# embedding 模型 bge-m3 (约 4.3GB, 本地缺失 → 索引/检索直接报错, 必装)
modelscope download --model BAAI/bge-m3 --local_dir D:/__WorkSpace__/modelscope/models/BAAI/bge-m3

# rerank 模型 bge-reranker-v2-m3 (本地缺失 → 检索自动降级不精排,
# 启动日志 warning 明示缺失路径; 下载后重启进程生效)
modelscope download --model BAAI/bge-reranker-v2-m3 --local_dir D:/__WorkSpace__/modelscope/models/BAAI/bge-reranker-v2-m3
```

- 模型变量可配绝对路径（如 `CHARPLOT_EMBEDDING_MODEL_NAME="D:/__WorkSpace__/modelscope/models/BAAI/bge-m3"`, 当前 .env 默认）或 `org/name` 风格名（自动解析到 `MODELSCOPE_ROOT` 下）
- 解析实现：`api/config.py::resolve_local_model_path`（存在 `config.json` 才算命中）

### 5.4 构建与启动

```bash
# 0. 前置: 项目根 .venv 已含全部依赖; Django 已跑在 8000 (含 app/charplot)

# 1. 启动 FastAPI AI 能力端 (仓库根目录执行, 以 project.charplot 包方式导入)
python -m project.charplot.api.server        # 127.0.0.1:8004, /ai/health 健康检查

# 2. 启动前端 (可选)
cd project/charplot/frontend && npm run dev  # 127.0.0.1:9004
```

### 5.5 测试

```bash
# FastAPI 侧 (Redis /15 隔离 + Fake LLM, 无外部网络依赖; 需本机 Redis)
cd project/charplot && pytest

# Django 侧 (需本机 MySQL/Redis, settings.dev)
python manage.py test app.charplot           # 277 用例
```

---

## 6. 业务状态（已全部闭环）

> 按垂直切片独立开发/测试/验收。

| 内容 | 状态 |
|------|------|
| 三端骨架 + 健康检查 / 账号体系与个人主页 | ✅ |
| 旅程创建链路 + stub 管道 + **全链路数据契约** | ✅ |
| 技能树地图 / 闯关答题与通关结算闭环 | ✅ |
| 复盘报告 + slug 公开分享页 + OG 卡片 | ✅ |
| 真实知识管道（LangGraph + DeepAgents + 检索源抽象替换 stub） | ✅ |
| 真实题目生成 + 间隔复习混入 + Boss 标记 | ✅ |
| 知识库管理链路 / 真实 Milvus 索引 + 混合检索 + rerank + 软删过滤 | ✅ |
| 主题卡片 + KB 驱动旅程 / 分析 Dashboard / LLM 状态总结 / 题目反馈标记 | ✅ |

**2026-09-08 业务完整性审查**：三端（FastAPI 7 端点 + 13 内部调用 / Django 12 表 41 路由 / 前端 11 页 30 API 调用）与数据契约逐条核对一致，无断链；早期 stub 产物（`pipeline/stub.py`、`services._stub_questions`）已于 2026-10-03 清理出仓库（归档在仓库外 `Temp/charplot-c03/`，零引用不留痕）。

---

## 7. 已知问题与改进建议

> 2026-09-08 审查列出的问题，除标「保留」外均已在 **2026-10-03（C03）** 处置；下表为处置结果。

### 7.1 韧性（C03 已修）

| 位置 | 问题 | 处置 |
|------|------|------|
| frontend: LevelList / QuizView / KBManage | SSE 任务丢失（FastAPI 重启等）后卡死在 generating/indexing 态且无恢复入口 | 三页进入时 `getTaskStatus` 探测任务存活（SSE 断流后再探一次确认），任务已死 → QuizView「生成任务已丢失」视图、LevelList 卡片「生成已中断 · 重新生成」、KBManage 索引中放开「重新索引」；三页都有退出路径 |
| api/tasks.py | 服务重启后任务 hash 仍在 running、事件 LIST 不再增长 → 订阅方无限空转 | 新增孤儿任务回收（判据: 无新事件 + 执行体不在进程注册表 + 状态 running）: 写终止 error 事件 + 实体推回失败态；单进程部署为前提 |
| api/server.py `/ai/kb/search` | RuntimeError（Milvus / 模型 / Django 不可达）直达 500，注释承诺 503 | 按注释实现：→ 503（服务端暂时不可用，前端可重试） |
| api/tasks.py | 管道失败时 error 事件 `progress` 恒 0（`last_progress` 死变量） | 三个任务执行体统一报「崩溃前那一阶段的进度」（读任务 hash 的 progress；孤儿回收同理） |

### 7.2 Django 规则层边界（C03 已修）

| 位置 | 问题 | 处置 |
|------|------|------|
| services.py / views_api.py | 脏数据兜底抛基类 `LevelError` → 500 而非 400 | 答题视图改捕基类 `LevelError`（新增子类自动 400），与其余业务异常一致 |
| views_api.py 重开接口 | 只拦 cleared，进行中（hearts>0）可重置进度重答刷 XP | 加失败态校验：hearts>0 → 400「本关未处于失败状态」 |
| services.py `_review_candidates` | 复习衰减用 UTC `.date()`，Dashboard 用 `localdate()`，凌晨窗口差 1 天 | 统一到 `localdate()`（本地自然日语义） |
| services.py update-in-place | 尾部删题未逐题确认无 Attempt，极端序列可能连带删历史 | 删前按题判 `Attempt` 存在性，有历史记录的题保留在题库尾部 |
| admin.py | `CharplotReviewReport` 未注册后台 | 已注册（只读快照: 人工核对分享页 / OG 卡片的入口） |

### 7.3 小缺陷与清理（C03 已修）

- HeartsBar 扣心动画指错心（倒序索引 → 改为 `flying = now`）
- QuizView 关卡加载失败残留 loading 骨架（→ 收敛为「关卡加载失败」错误视图）
- 退役 stub 清理：`pipeline/stub.py`、`services._stub_questions`（零引用，归档到仓库外 `Temp/charplot-c03/`）
- 过时注释/文案同步（KBManage "stub 索引"、QuizView "来源引用待接入"、Profile "闯关答题未上线"、SkillNode "本期恒为空"、client.ts "全量重建 stub" 等）
- `client.ts` 未使用导出接线：`checkDjangoHealth` / `checkAiHealth` → 顶部导航双后端健康指示；`getTaskStatus` → 三页任务存活探测
- rerank 表述口径：README 不再写「必配」；`/ai/health` 增加 `rerank` 字段（`{degraded, reason|model}`），架构意图与运行时事实并列可见

### 7.4 架构级取舍与风险（有意识, 非缺陷）

- **同步阻塞在事件循环**：pipeline kb 检索、bge-m3 encode（模型 4.3GB, 首次加载 + CPU 推理）、FlagReranker 全为同步调用跑在 async 事件循环上, 首次加载/推理最长可冻结 Redis 心跳数十秒；单机自用可接受, SSE 断线靠 Last-Event-ID 续推兜底（有测试覆盖）
- **孤儿回收依赖单进程**：判定「执行体不在注册表」在多 worker 部署下会误判（别的 worker 里的活任务被当孤儿）；本项目 `uvicorn.run` 单进程启动, 若将来加 worker 需把判据换成跨进程心跳
- **agents/ 覆盖最小**：`build_search_tools` 的闭包绑定有测试钉住（循环变量捕获回归）；真实 `create_deep_agent` 执行路径仍需真模型且输出非确定，只靠运行期验证（测试用 Fake 替换, conftest）
- rag/__init__.py 顶层 re-export 使 import 顺序敏感（kb_source → rag.retriever → pipeline 环）, 当前入口顺序无环
- 有意降级（非未完成）：Tavily key 缺失跳网络源 / rerank 本地模型缺失降级不精排（`/ai/health` 可见）/ rewrite 失败用原 query / kb 旅程绕过 subagent 走确定性 KbSource 检索

---

## 8. Phase 2（明确不做 / 二期）

视频输入（复用 video_downloader 转录）、代码题与对话式 Boss 战（LLM 评判组件）、成就勋章、排行榜、增量索引、Agentic RAG（corrective/adaptive, 在 `rag/` 模块内演进、外部接口不变）。

---

> 首次纳入文档：2026-09-08（业务完整性审查后补写）｜ 维护者：Claude Code (charlotte)
