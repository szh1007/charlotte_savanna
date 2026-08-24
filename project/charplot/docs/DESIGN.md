# DESIGN.md — CharPlot 设计方案

> 状态：待用户终审。确认后按「分步实施计划」开发（对应 `.scratch/charplot/issues/` tickets）。
> 领域术语参见 [CONTEXT.md](./CONTEXT.md)，架构决策参见 [docs/adr/](./adr/)。
> 需求见 `.scratch/charplot/PRD.md`，技术规格见 `.scratch/charplot/SPEC.md`。

---

## 1. 产品定位

**CharPlot**：输入想学的任何知识（一句话 / 一段话 / 文档 / 网页链接）→ AI 联网获取知识 → 解构成技能树图谱 → 渐进生成闯关题目 → 游戏化答题（Duolingo 式）→ 通关复盘报告可分享。

核心价值：
- **低门槛启动**：一句话就能开学（"我想学 Python 装饰器" → 3 分钟后开始第一关）
- **游戏化驱动**：闯关 / 连胜 / 技能树点亮，把数月学习的延迟满足拆解为即时正向激励
- **无痛巩固**：间隔复习在生成新关时动态混入 20% 历史易错知识点
- **有据可依**：每题附来源引用，答对答错都有讲解

UI 基调：**轻松动漫风**。B 站粉 `#FB7299` 主色 + 二次元柔和色系（奶白 / 浅紫 / 淡蓝），圆角卡片、软阴影、温和反馈动画（答错是鼓励而非红叉）。

定位：自用学习项目，但架构按真实产品设计（账号体系 / 分享 / 部署齐全）；付费商业机制一律降级为轻量化实现（连胜冻结卡 → 学习币兑换）。

## 2. 架构总览

**双后端微服务**（ADR-0001）——职责切分：Django = 状态与数据，FastAPI = AI 能力。

```
┌─────────────── Vue 3 前端 (project/charplot/frontend) ───────────────┐
│  Element Plus (B站粉动漫风主题) + 图渲染库(技能树) + 答题动画自定义组件    │
└──────┬──────────────────────────┬──────────────────────────┬─────────┘
       │ HTTP                     │ HTTP + SSE               │ HTTP
┌──────▼──────────────┐   ┌───────▼─────────────────────────┐ │
│ Django (app/charplot)│   │ FastAPI (project/charplot)      │ │
│ 账号体系 (auth_user) │   │ AI 能力端:                      │ │
│ 学习数据 (charplot_*)│◄──┤ 知识管道(LangGraph 编排+subagent)│ │
│ 闯关交互(判分/游戏化) │   │ RAG 全链路(索引/混合检索/rerank) │ │
│ 后台分析 Dashboard   │   │ 任务系统(异步+Redis+SSE)         │ │
│ 知识库元数据         │   │ 题目生成(渐进+间隔复习混入)       │ │
│ 公开分享页 (slug)    │   │ Boss 战对话流式(二期)            │ │
└──────┬──────────────┘   └───────┬─────────────────────────┘ │
       │        共享 MySQL         │        共享 Milvus / Redis   │
       └───────────────────────────┴───────────────────────────┘
```

- **Django 侧**：Django 6.0 + DRF + MySQL + Redis；闯关交互归 Django（ADR-0003，首版判分/游戏化是纯规则 + 预生成讲解，无 LLM）
- **FastAPI 侧**：FastAPI + LangChain + LangGraph + DeepAgents（三件套实践）；RAG 全链路归 FastAPI（Q9b）
- **共享**：MySQL（Django ORM 管 schema，FastAPI 只读题/写记录需经 Django API）、Redis（任务状态）、Milvus（向量）
- **异步编排**：FastAPI 后台任务 + Redis 任务状态 + SSE 进度推送，失败自动重试；预生成复用同一任务系统

## 3. 目录结构

```
app/charplot/                        # Django 侧（业务）
├── models.py                        # 11 张表（charplot_ 前缀）：profile/knowledge_base/
│                                    #   knowledge_base_document/journey/chapter/knowledge_point/
│                                    #   level/question/attempt/review_report/user_event
├── views_api.py / views_html.py     # DRF API / 公开分享页 CBV
├── services.py                      # 判分 / 游戏化规则（XP/连胜/心动值/易错分/学习币）
├── serializers.py / cache.py / signals.py / permissions.py
├── urls_api.py / urls_html.py
├── migrations/ / tests/
└── dashboard/                       # 后台分析聚合查询

project/charplot/                    # FastAPI 侧（AI 能力）
├── api/                             # server.py / schemas.py / tasks.py(任务系统+SSE)
├── pipeline/                        # 知识管道：parse → search → deconstruct（LangGraph StateGraph）
├── agents/                          # DeepAgents subagents（搜索/解构/出题）
├── rag/                             # 索引(切分/embedding/Milvus) / 检索(混合+rerank) / 软删过滤
├── kb/                              # 知识库索引任务 + 进度
├── prompt/                          # prompt 配置（解构/出题/讲解/总结）
├── tests/
└── docs/                            # CONTEXT.md / DESIGN.md / adr/

project/charplot/frontend/           # Vue 3 + Vite + TS 前端
├── src/
│   ├── styles/theme.css             # 设计令牌（B站粉/柔和色/圆角/阴影/字体）
│   ├── api/client.ts                # fetch 封装 + SSE 客户端
│   ├── components/                  # 技能树图/答题卡/进度条/结算动画/主题卡片…
│   ├── views/                       # 主题列表/闯关地图/答题/结算/复盘/个人主页/Dashboard/管理
│   └── router/
```

## 4. API 设计

### 4.1 Django 侧（业务，`/api/...`）

| 方法 | 路径 | 说明 | 请求 → 响应 |
|------|------|------|-------------|
| POST | `/api/auth/register` / `/api/auth/login` / `/api/auth/logout` | 账号（Django 内置认证） | → 会话 / token |
| GET | `/api/profile` | 个人主页：XP/等级/连胜/最大连胜/心动值/学习币/统计面板 | → Profile |
| POST | `/api/profile/streak-freeze` | 学习币兑换连胜冻结 | → `{coins, frozen}` |
| POST | `/api/journeys` | 创建旅程：文本 / 文件 / 网页链接 / 知识库 | `{input_type, content?, kb_id?}` → `{journey_id}` |
| GET | `/api/journeys` | 旅程列表（进行中/已通关） | → `{journeys[]}` |
| GET | `/api/journeys/{id}` | 旅程详情 + 图谱 + 生成任务状态 | → Journey |
| GET | `/api/journeys/{id}/skill-tree` | 技能树数据（节点/依赖边/点亮状态） | → `{nodes[], edges[]}` |
| GET | `/api/journeys/{id}/levels` | 关卡列表（按知识点+序号） | → `{levels[]}` |
| POST | `/api/levels/{id}/answer` | 提交答案：判分 + 讲解 + 心动值扣减 | `{question_id, answer}` → `{correct, explanation, sources[], hearts}` |
| POST | `/api/questions/{id}/flag` | 「题目有问题」标记（重复幂等去重） | `{reason?}` → 200 `{created}` |
| GET | `/api/journeys/{id}/report` | 复盘报告数据 | → Report |
| GET | `/r/{slug}` | 公开分享页（只读，无登录可看，OG 标签） | → 页面 |
| POST | `/api/kb` | 管理员创建知识库（is_staff） | `{name, desc, cover}` → KB |
| POST | `/api/kb/{id}/documents` | 上传文档 | → Document |
| DELETE | `/api/kb/documents/{id}` | 软删文档（可恢复） | → 204 |
| POST | `/api/kb/{id}/reindex` | 触发全量重建 | → `{task_id}` |
| GET | `/api/kb` | 知识库列表（管理员含全部状态 / 用户端仅就绪） | → `{kbs[]}` |
| GET | `/api/topics` | 主题卡片（就绪知识库） | → `{topics[]}` |
| GET | `/api/dashboard/mastery` / `/activity` / `/weakpoints` | 掌握度矩阵 / 活动统计 / 易错清单 | → 聚合数据 |

### 4.2 FastAPI 侧（AI，`/ai/...`）

| 方法 | 路径 | 说明 | 请求 → 响应 |
|------|------|------|-------------|
| POST | `/ai/pipeline` | 启动知识管道（解析→搜索→解构→图谱落库） | `{journey_id, input_type, content?}` → `{task_id}` |
| GET | `/ai/tasks/{id}` | 任务状态 | → `{status, stage, progress}` |
| GET | `/ai/tasks/{id}/events` | SSE 进度流（stage 变更 + 进度） | → 事件流 |
| POST | `/ai/levels/generate` | 渐进出题（含间隔复习混入 Top 20%） | `{journey_id, level_seq}` → `{task_id}` |
| POST | `/ai/kb/index` | 知识库索引任务（切分/embedding/入库） | `{kb_id}` → `{task_id}` |
| POST | `/ai/kb/search` | 混合检索 + rerank（管道内部/调试） | `{kb_id, query}` → `{chunks[]}` |
| POST | `/ai/report/summary` | LLM 状态总结（统计聚合 → 文字报告） | `{user_id}` → `{summary}` |

**SSE 事件**（`event: pipeline-progress`）：`{task_id, stage, progress, message}`——阶段：`parsing → analyzing → searching → deconstructing → done/error`；索引任务：`parsing → chunking → embedding → indexing → done/error`；出题任务（Issue 08）：`preparing → generating → saving → done/error`。任务 HASH 带 `task_type`（pipeline / level-generation），`GET /ai/tasks/{id}` 返回。

## 5. 业务规则（后端强制）

| 规则 | 实现 |
|------|------|
| 判分 | 选择/判断精确匹配；填空模糊匹配（归一化：去空白/大小写/全半角） |
| 心动值 | 每关 5 心，答错 -1；退出关卡保留剩余心，再次进入续答；扣完 = 本关失败需重开（重开重置）（「安全失败」） |
| XP/等级 | 答对 +X、通关 +Y（规则参数集中配置，等级阈值表） |
| 连胜 | 自然日 +1；中断显示损失警告；冻结（学习币兑换）不中断 |
| 易错分 | 知识点级：答错 +2、答对 -1、下限 0 |
| 间隔复习 | 新关生成时按「易错分 × 时间衰减」排序混入 Top 20%（复用历史题目与讲解，不新生成） |
| 学习币 | 通关发放；兑换连胜冻结 |
| 知识库 | 状态机 `Draft → Indexing → Ready`（`Failed` 可重试、可下线）；变更触发全量重建 |
| 软删除 | Django `is_deleted` + Milvus metadata 有效标记，检索 filter 排除；移除立即生效，重建物理剔除 |
| 权限 | 知识库管理限 `is_staff`；分享页只读防篡改 |
| 幻觉防护 | 题目/讲解只基于检索片段生成（prompt 约束）+ 来源引用 + 「题目有问题」标记 |

## 6. UI 设计规范

**设计令牌**（`theme.css`）：

| 令牌 | 值 | 用途 |
|------|-----|------|
| `--primary` | `#fb7299` B 站粉 | 主色（B 站品牌色） |
| `--accent` | 奶白 `#fff5f8` / 浅紫 / 淡蓝 | 柔和辅助色 |
| `--bg` | 浅粉 → 浅蓝渐变 | 全局背景 |
| `--card` | `#ffffff` | 白卡 |
| 圆角 | `16px` | 卡片 / 输入框 |
| 阴影 | 软阴影（低透明度大模糊） | 浮层感 |
| 字体 | 系统栈 + 圆润感 | 轻松氛围 |

**页面结构**：

1. **导航**：Logo + XP/等级/连胜/心动值/学习币（实时）+ 个人主页入口
2. **首页（主题/旅程列表）**：Hero（大标题 + 一句话输入框 + 上传区）+ 主题卡片墙（管理员预建知识库，就绪态可点）+ 我的旅程列表
3. **生成进度页**：阶段化 SSE 进度（解析→搜索→解构→完成），失败可重试
4. **闯关地图页**：技能树图渲染（依赖边 + 通关点亮动画，节点=知识点，多关合并进度）+ 关卡入口
5. **答题页**：题目卡（选择/判断/填空）+ 即时反馈（答对彩花 / 答错温和鼓励动画 + 讲解 + 来源引用）+ 心动值显示 + Boss 标记关高难度样式
6. **通关结算页**：XP/学习币/连胜结算 + 彩花动画 + 节点点亮 + 复盘报告入口
7. **复盘报告页**：知识总结 + 答题复盘 + 分享按钮（公开链接 + OG 卡片）
8. **个人主页**：统计面板（登录天数/已学/在学章节/答题统计）+ 连胜冻结兑换
9. **分析 Dashboard**：掌握度矩阵（薄弱点高亮）/ 活动统计 / 易错清单 / LLM 状态总结
10. **管理员知识库管理页**（is_staff）：创建/上传/文档列表（软删恢复）/索引进度/状态机/下线

## 7. 分步实施计划

> 对应 `.scratch/charplot/issues/01 ~ 14`（垂直切片，每步独立开发/测试/验收）。`→` 为依赖链。

| 步骤 | 内容 | 验证项 |
|------|------|--------|
| 01 | 三端骨架：Django app（charplot_ 前缀 + profile 表）、FastAPI 服务、Vue 工程、共享 MySQL/Redis 配置、健康检查；前端视觉基座（B站粉主题令牌） | 三端可启动，最小请求链路打通 |
| 02 | 账号体系：注册/登录/登出 + charplot_profile 自动创建 + 个人主页（等级/XP/连胜/心动值/学习币 + 兑换入口 + 基础统计面板） | 注册登录全流程；主页字段实时同步 |
| 03 | 旅程创建链路：输入 → FastAPI stub 管道（示例图谱）→ SSE 阶段进度 → Journey/Chapter/KP 落库 → 旅程列表；**定义全链路数据契约** | 创建旅程 → 进度流 → 图谱落库 → 列表可见 |
| 04 | 技能树地图：图谱可视化（节点/依赖边/点亮状态）+ 关卡入口（图渲染库选型） | 地图渲染正确，依赖边与点亮状态正确 |
| 05 | 闯关答题：stub 题目 → 判分/讲解/心动值/温和反馈 → 通关结算（XP/学习币/连胜/点亮）+ Attempt/用户事件落库 | 完整答题闭环 + 数字与规则一致 |
| 06 | 复盘报告：通关生成报告 + slug 公开只读页 + OG 社交卡片 | 未登录可访问分享页 |
| 07 | 真实知识管道（并行于 04-06）：LangGraph 编排（解析文档/链接 → 联网搜索增强 → 图谱解构）+ 检索源抽象（网络/Context7/知识库/文档）替换 03 stub | 真实材料生成合理图谱，契约不变 |
| 08 | 真实题目生成 + 间隔复习 + Boss 标记（并行于 06）：选择/判断/填空生成（讲解+来源引用预留）+ 易错分混入 Top 20% + 预生成机制 | 真实题目可答；复习题无感混入 |
| 09 | 知识库管理链路：管理员页（创建/上传/文档管理/软删恢复/状态机）+ stub 索引任务进度 | is_staff 全流程；用户端不可见未就绪库 |
| 10 | Milvus 索引与检索：真实切分/embedding/入库 + 混合检索 + query rewriting + rerank + 软删过滤（替换 09 stub） | 检索命中正确；软删文档不命中 |
| 11 | 主题卡片 + 知识库驱动旅程：就绪库卡片页 + 点击直达 + RAG 两轮解构（知识库为主内容） | 点击主题 → 生成知识库旅程 |
| 12 | 分析 Dashboard：掌握度矩阵 + 活动统计 + 易错清单（事实聚合） | 数字与 Attempt/事件一致 |
| 13 | LLM 状态总结：聚合 → FastAPI → LLM 文字报告 | 点击生成有效总结 |
| 14 | 题目反馈标记：标记落库 + Dashboard/管理侧可见 | 标记后后台可见 |

## 8. 明确不做（Phase 2 计划）

- 付费机制（连胜冻结卡 / 心动值充值）—— 学习币兑换轻量替代
- 排行榜 / 成就勋章 —— 单人自用无运营价值，后置
- 增量索引 —— 全量重建 + 软删除机制已覆盖需求（doc_id 级增量设计为知识储备）
- 多实例部署 / 任务持久化 —— 单机异步任务系统
- 视频输入 / 代码题 / 对话式 Boss 战 —— Phase 2，见 §9

## 9. Phase 2 能力（二期）

| 能力 | 说明 |
|------|------|
| 视频输入 | 复用 video_downloader 转录能力（下载/音频/Whisper/VAD 分段），入口归一化为转录文本 |
| 代码题 | 「LLM 评判」组件（与 Boss 战对话共用），代码执行沙箱 |
| 对话式 Boss 战 | 章节末尾 AI 角色扮演（如"严格的代码评审员"），FastAPI 流式 |
| 成就勋章 | 条件系统 + 图标 |
| 排行榜 | 好友对比（需多用户活跃） |
| 增量索引 | 企业级：doc_id 级更新/删除（软删除机制已提前设计） |
| Agentic RAG | LangGraph 编排智能检索（类似 RAGFlow）：检索质量评估 / 失败重写重试 / 按需检索；rag/ 模块内演进，外部接口不变 |
