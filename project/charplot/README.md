# CharPlot — AI 闯关学习网站（双后端微服务）

> 输入想学的**任何知识**（一句话 / 一段话 / 文档 / 网页链接 / 管理员预建知识库）→ AI 联网获取知识 → 解构成技能树图谱 → 渐进生成闯关题目 → 游戏化答题（Duolingo 式：心动值 / 连胜 / XP）→ 通关复盘报告可分享。
>
> 架构按**真实产品**设计（账号体系 / 分享页 / 后台分析 Dashboard 齐全），付费商业机制（连胜冻结卡等）一律降级为学习币兑换的轻量化实现。
>
> 业务术语与产品决策见 [docs/CONTEXT.md](./docs/CONTEXT.md)（权威术语表）；接口与数据结构契约见 [docs/CONTRACT.md](./docs/CONTRACT.md)；方案设计见 [docs/DESIGN.md](./docs/DESIGN.md)；架构问答索引见 [docs/QA.md](./docs/QA.md)；决策记录见 [docs/adr/](./docs/adr/)。

---

## 1. 项目概览

| 能力 | 说明 |
|------|------|
| 知识获取 | 统一知识管道（ADR-0002）：输入归一化解析（txt/md/html/pdf/docx/pptx/网页链接）→ LLM 主内容分析 → 联网搜索增强 → 图谱解构 |
| 知识图谱 | 章节 → 知识点（带前置依赖边），技能树 / 关卡 / 间隔复习全部锚定图谱 |
| 闯关学习 | 关卡按知识点粒度渐进生成（3 分钟/关），选择 / 判断 / 填空三题型，5 心动值安全失败机制，断点续答，通关结算 |
| 间隔复习 | 新关生成时混入 Top 20% 历史易错题（易错分 × 时间衰减规则调度，无 LLM 参与） |
| 复盘报告 | 通关后生成知识总结，slug 公开只读分享页（无登录可看 + OG 卡片） |
| 知识库（RAG） | 管理员预建知识库（文档上传 / 软删 / 全量重建），主题卡片直达 Journey；企业级检索链路：解析 → 按类型调优切分 → bge-m3 embedding → Milvus 混合检索 → query rewrite → **rerank 必配** → 带引用生成 |
| 游戏化 | XP / 等级 / 连胜（冻结卡降级为学习币兑换）/ 心动值 / 学习币，全部规则层后端强制 |
| 后台分析 | 掌握度矩阵 / 活动统计 / 易错清单（事实聚合）+ LLM 文字版状态总结 |
| 幻觉防护 | 三层：题目讲解只基于检索片段 + 来源引用展示 + 「题目有问题」反馈标记 |

### 技术栈

| 组件 | 技术 |
|------|------|
| 状态与数据端 | Django 6.0 + DRF + MySQL + Redis（`app/charplot`, 主项目 8000 端口） |
| AI 能力端 | FastAPI 0.139（端口 8004） |
| 三件套分工 | LangGraph = 知识管道编排（`pipeline/`）· DeepAgents = 检索/出题 subagent（`agents/`）· LangChain = RAG 组件（`rag/`） |
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

**双后端微服务（ADR-0001）**：Django = 状态与数据（账号体系 / 学习数据 / 闯关交互规则 / 知识库元数据 / Dashboard / 分享页）；FastAPI = AI 能力（知识管道 / RAG 全链路 / 题目生成 / 任务系统）。闯关交互归 Django（ADR-0003：判分与游戏化是纯规则 + 预生成讲解，**LLM 不参与答题路径**）；RAG 全链路归 FastAPI，Django 只存知识库元数据。

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

- **服务间通信**：FastAPI 调 Django 内部端点一律 `X-Internal-Token`（`CHARPLOT_INTERNAL_TOKEN`, 两端 .env 同值, 未配置 fail closed）；**不存在 Django → FastAPI 反向调用**（索引/出题触发由前端直调 `/ai/*`, CONTRACT §6.3 决策）
- **共享存储**：MySQL schema 归 Django ORM 管理；FastAPI 读题/写学习数据一律经 Django 内部端点，不直连库

### AI 能力全景（LLM 参与的五个流程）

| 流程 | 编排 | 状态 |
|------|------|------|
| A. 知识管道: 解析(无 LLM) → 主内容分析 → 联网搜索增强 → 图谱解构 | LangGraph StateGraph 编排, 检索环节套 DeepAgents | ✅ Issue 07 |
| B. RAG: 索引(批处理) → 检索(rewrite → 混合 → rerank → Top-K) | LangChain 管线式, 被动服务 | ✅ Issue 10 |
| C. 题目生成: 知识点 + 检索片段 → 题目 JSON(讲解+来源引用) | DeepAgents 出题 subagent | ✅ Issue 08 |
| D. 闯关答题: 判分/心动值/间隔复习混入 | 纯规则, **无 LLM** | ✅ Issue 05 |
| E. LLM 状态总结: 统计聚合 → 文字报告 | 裸 LLM 调用 | ✅ Issue 13 |

> 关键设计：**RAG 只返回片段不生成答案**（QA.md Q7）——生成动作（图谱/题目/讲解）全在 A/C 的 LLM 环节，「生成依据」与「生成动作」拆开，实现幻觉防护。

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
├── rag/                             # 索引(chunking/embeddings/milvus) + 检索(retriever/
│   │                               #   query_rewrite/rerank)
├── prompt/                          # prompt 配置(analyze/search/deconstruct/questions/status_summary)
├── frontend/                        # Vue 3 + Vite + TS (11 views: Home/闯关地图/答题/复盘/
│   │                               #   Profile/Dashboard/KBManage/Login…)
├── docs/                            # CONTEXT / CONTRACT / DESIGN / QA + adr/0001~0004
├── tests/                           # FastAPI 侧 9 个测试文件 (Redis /15 隔离 + Fake LLM, 不触网)
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
└── migrations/ (9) / tests/ (265 用例)

.scratch/charplot/                   # [已归档 2026-09-18 移出仓库] 需求与追踪: PRD.md / SPEC.md / issues/01~14
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

SSE 事件统一 `pipeline-progress`，每帧带递增 `id`，断线重连按 `Last-Event-ID` 增量续推。任务不持久化（FastAPI 重启丢失 → 前端兜底「重新生成」）。失败语义：落库写自动重试 1 次（transient 5xx/连接错误）。

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
python manage.py test app.charplot           # 265 用例
```

---

## 6. 业务状态（Issue 01~14 已全部闭环）

> 每个 Issue 独立开发/测试/验收（垂直切片），ticket 原在 `.scratch/charplot/issues/`（2026-09-18 随归档移出仓库，仅在 git 历史中）。

| Issue | 内容 | 状态 |
|-------|------|------|
| 01~02 | 三端骨架 + 健康检查 / 账号体系与个人主页 | ✅ |
| 03 | 旅程创建链路 + stub 管道 + **全链路数据契约** (CONTRACT.md) | ✅ |
| 04~05 | 技能树地图 / 闯关答题与通关结算闭环 | ✅ |
| 06 | 复盘报告 + slug 公开分享页 + OG 卡片 | ✅ |
| 07 | 真实知识管道（LangGraph + DeepAgents + 检索源抽象替换 stub） | ✅ |
| 08 | 真实题目生成 + 间隔复习混入 + Boss 标记 | ✅ |
| 09~10 | 知识库管理链路 / 真实 Milvus 索引 + 混合检索 + rerank + 软删过滤 | ✅ |
| 11~14 | 主题卡片 + KB 驱动旅程 / 分析 Dashboard / LLM 状态总结 / 题目反馈标记 | ✅ |

**2026-09-08 业务完整性审查**：三端（FastAPI 7 端点 + 13 内部调用 / Django 12 表 41 路由 265 测试 / 前端 11 页 30 API 调用）与 CONTRACT 契约逐条核对一致，无断链、无 stub 参与运行时；`stub.py`（Issue 03 退役产物）与 `services._stub_questions`（Issue 05 遗留）为有意保留的死代码。

---

## 7. 已知问题与改进建议（2026-09-08 审查）

### 7.1 韧性缺口（最值得修）

| 位置 | 问题 |
|------|------|
| frontend: LevelList / QuizView / KBManage | SSE 任务丢失（FastAPI 重启等）后卡死在 generating/indexing 态且无恢复入口：QuizView 无退出/重试按钮、LevelList 卡片无兜底动作、KBManage indexing 态禁用重试按钮（后端允许超 10 分钟重抢, UI 无入口）。CONTRACT §2 承诺「SSE 404 → 前端兜底重新生成」仅 JourneyDetail 实现。`client.ts:691 getTaskStatus` 轮询函数已封装未接线, 可直接补 |
| api/server.py:167-171 | `/ai/kb/search` 的 RuntimeError（Milvus/Django 不可达/模型加载失败）直达 500, django_client.py:291 注释承诺转 503 —— 注释与实现不一致 |
| api/tasks.py:201 | 管道 error 事件 progress 恒 0（`last_progress` 死变量, 应报崩溃前阶段 60） |

### 7.2 Django 规则层边界（低危）

| 位置 | 问题 |
|------|------|
| services.py:1063 / views_api.py:373 | 脏数据兜底抛基类 `LevelError` → 500 而非 400（应 catch 基类或改抛子类） |
| views_api.py:393-399 | 重开接口只拦 cleared, 进行中（hearts>0）可任意重置进度重答刷 XP（收益有界: 10 级封顶, 建议加 hearts<=0 失败态校验） |
| services.py:699-700 | 复习衰减用 UTC `.date()`, dashboard.py:220 用 `localdate()` —— 凌晨窗口复习排序与弱项清单可能差 1 天, 建议统一 |
| services.py:906-936 | update-in-place 尾部删题未按题确认无 Attempt, 极端序列（重开答全旧题 + 重生成题数变少）可能连带删除历史 Attempt |
| admin.py | CharplotReviewReport 未注册后台（其余 11 模型均已注册） |

### 7.3 小缺陷与清理

- HeartsBar.vue:19 扣心动画指错心（`flying = max - now - 1` 倒序索引, 应为 `now`）
- QuizView.vue:145-147 关卡加载失败残留 loading 骨架（应收敛错误视图）
- pipeline/stub.py、services.py:490-586 `_stub_questions`：退役 stub, 保留参考或清理
- 过时注释/文案多处（KBManage "stub 索引"、QuizView "来源引用待接入"、Profile "闯关答题未上线"、SkillNode "本期恒为空"、client.ts 596 等）—— 功能早已真实实现, 注释未同步
- client.ts 未使用导出：`checkDjangoHealth`/`checkAiHealth`/`getTaskStatus`

### 7.4 架构级取舍与风险（有意识, 非缺陷）

- **同步阻塞在事件循环**：pipeline kb 检索、bge-m3 encode（模型 4.3GB, 首次加载 + CPU 推理）、FlagReranker 全为同步调用跑在 async 事件循环上, 首次加载/推理最长可冻结 Redis 心跳数十秒；单机自用可接受, SSE 断线靠 Last-Event-ID 续推兜底（有测试覆盖）
- **agents/（DeepAgents 0.7 编排）无测试覆盖**：真实 `create_deep_agent` 执行只靠运行期验证, 测试用 Fake 替换（conftest）
- rag/__init__.py 顶层 re-export 使 import 顺序敏感（kb_source → rag.retriever → pipeline 环）, 当前入口顺序无环
- 有意降级（非未完成）：Tavily key 缺失跳网络源 / rerank 留空不精排 / rewrite 失败用原 query / kb 旅程绕过 subagent 走确定性 KbSource 检索（QA.md Q7、Q8）

---

## 8. 文档体系

| 文档 | 内容 |
|------|------|
| [docs/CONTEXT.md](./docs/CONTEXT.md) | 领域术语（Language 表）+ 产品决策记录 Q1~Q22 |
| [docs/CONTRACT.md](./docs/CONTRACT.md) | 数据契约 v1: 图谱 JSON / 任务 SSE / 内部端点 / 知识库状态机 |
| [docs/DESIGN.md](./docs/DESIGN.md) | 架构总览 / API 设计表 / 业务规则表 / UI 规范 / 分步实施计划 |
| [docs/QA.md](./docs/QA.md) | 架构问答索引（三件套分工 / 流程链路 / 关键决策速查） |
| [docs/adr/](./docs/adr/) | 0001 双后端 / 0002 统一管道 / 0003 闯关交互归 Django / 0004 图谱图库 |
| `.scratch/charplot/`【已归档 2026-09-18 移出仓库】 | 原为 PRD.md / SPEC.md / issues/01~14（需求与验收源头），现仅在 git 历史中 |

## 9. Phase 2（明确不做 / 二期）

视频输入（复用 video_downloader 转录）、代码题与对话式 Boss 战（LLM 评判组件）、成就勋章、排行榜、增量索引、Agentic RAG（corrective/adaptive, 在 `rag/` 模块内演进、外部接口不变）—— 详见 [docs/DESIGN.md §8~9](./docs/DESIGN.md)。

---

> 首次纳入文档：2026-09-08（业务完整性审查后补写）｜ 维护者：Claude Code (charlotte)
