# 11 — 主题卡片与知识库旅程

**Status:** done

**Blocked by:** 10 — Milvus 索引与检索

**What to build:** 用户端主题卡片页（就绪知识库以卡片展示：主题名 / 描述 / 封面，B 站粉风格），点击直达启动学习旅程——以该知识库为主内容，RAG 两轮解构产出图谱（第一轮概览检索建图谱骨架 → 第二轮按知识点检索细化依赖边），解构 / 出题均吃检索。知识库驱动的旅程与用户自输入旅程共用同一知识管道契约。

**Acceptance criteria:**
- [x] 主题卡片页仅展示就绪知识库，卡片信息完整（封面/描述）
- [x] 点击主题卡片创建旅程，SSE 进度可见
- [x] RAG 两轮解构产出合理图谱（章节 + 知识点 + 依赖边），基于知识库内容
- [x] 知识库旅程可正常闯关（与自输入旅程体验一致）
- [x] 知识库文档更新后重新索引，新旅程反映最新内容

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 11；PRD C-4；SPEC §6.2 两轮解构 / Q19、Q19a

**实现摘要 (2026-08-24):**

**Django 侧**（`app/charplot/`）：
- `CharplotJourney` 增加 `kb` 输入类型 + `knowledge_base` FK（SET_NULL 保留旅程历史），migration 0008
- `JourneyCreateSerializer`：kb 类型必带 kb_id，校验知识库存在且就绪（非就绪 400 中文错误）；`create_journey` 服务层防御性复校验（覆盖 serializer 校验后知识库被下线/删除的竞态），标题取知识库名
- `JourneyDetailSerializer` 返回 `kb_id`（重试透传用）+ `knowledge_base` 嵌套（前端展示「基于知识库」标记）
- 新增内部端点 `GET /api/charplot/kb/{pk}/meta/`（IsInternalService）：返回 `{id, name, description, status}`，供 FastAPI parse 阶段取元信息并在知识库非就绪时快速失败

**FastAPI 侧**（`project/charplot/`）：
- 输入契约扩展：`InputType` 加 `"kb"`，`PipelineRequest`/`PipelineInput` 加 `kb_id`（仅 kb 类型可携带，否则 422），`create_task` 透传
- `parse` 阶段 kb 分支：`django_client.fetch_kb_meta` 取元信息，名称+描述作为材料（analyze 输入）；知识库被下线/删除 → 快速失败
- `search` 阶段 kb 分支：不走 DeepAgents subagent（骨架轮需要确定的概览资料），对建议查询逐个确定性检索知识库（`build_sources(kb_id)` 仅注册 KbSource，不联网——QA Q8 知识来源 = Milvus 检索片段），失败降级
- **RAG 两轮解构**（`stages/deconstruct.py` + `prompt/deconstruct.py`）：
  - 第一轮骨架：概览检索资料 → LLM 建契约同构骨架（章节 + 知识点粗结构，prerequisites 可空），契约校验 + 重试反馈
  - 第二轮细化：逐知识点精检索（KbSource，同步收集——检索链路阻塞无并发收益）→ `asyncio.gather` + Semaphore(4) 并发 LLM 细化（补全摘要/依赖边/来源），三道校验防 LLM 改 id/非法引用（id 保持 + prerequisites 子集 + 整体契约）
  - 细化失败 fail-fast（不做质量回退），与既有解构语义一致

**前端**（`project/charplot/frontend/`）：
- `client.ts`：`JourneyInputType` 加 `'kb'`，`createJourney`/`startPipeline` 支持 kb_id，`JourneyDetail` 加 `kb_id`/`knowledge_base`
- `Home.vue`：主题卡片点击直达开旅程（登录检查 → 创建 kb 旅程 → 启动管道 → 详情页接管 SSE）；卡片 hover 提升 + 封面微缩放 + 「开始学习」按钮遮罩浮现 + loading 态 + 键盘可达（`role="button"`/tabindex/Enter/Space），视觉遵循既有 theme.css B 站粉令牌（`/frontend-design` 技能：签名交互点克制收敛）
- `JourneyDetail.vue`：kb 旅程展示「📚 基于知识库 · 名称」chip；失败重试透传 kb_id（知识库被删则提示不可重试）

**验收 4/5 说明（既有机制自动满足）**：知识库旅程就绪后走既有闯关链路（关卡懒创建/渐进出题/答题结算，与自输入旅程零差异）；知识库文档更新触发全量重建（Issue 10），新旅程检索实时查询 Milvus（软删过滤实时生效），自动反映最新内容。

**测试**：Django 新增 kb 创建（就绪成功 / 非就绪 400 / 缺 kb_id / 不存在 / 详情嵌套 / meta 端点 token 校验）8 用例；FastAPI 新增 kb 管道两轮解构（骨架 + 细化 + 重试反馈 + id 保持校验）、`build_sources` KbSource 注册、API 输入校验（缺 kb_id 422 / 非 kb 带 kb_id 422 / kb 全链路 done 落库）——Django 231 + FastAPI 70 全绿，ruff/codespell 干净。
