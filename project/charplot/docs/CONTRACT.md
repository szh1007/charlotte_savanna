# CharPlot 数据契约 (v1)

> **权威契约**：管道产出 → 落库 → API 暴露的图谱结构。Issue 03 定义，07（真实管道）/ 08（题目生成）必须遵循。
> **演进规则**：只增不改；结构性变更必须递增顶层 `version` 字段；旧版前端可继续消费旧字段。
> 术语见 [CONTEXT.md](./CONTEXT.md)，API 总览见 [DESIGN.md §4](./DESIGN.md)。

---

## 1. 图谱 JSON（管道产出 → 内部落库端点）

```json
{
  "version": 1,
  "title": "Python 装饰器",
  "chapters": [
    {
      "id": "ch_1",
      "title": "函数基础",
      "summary": "装饰器的前提知识",
      "knowledge_points": [
        { "id": "kp_1", "title": "函数是一等公民", "summary": "可传递可返回", "prerequisites": [] },
        { "id": "kp_2", "title": "闭包", "summary": "词法作用域", "prerequisites": ["kp_1"] }
      ]
    }
  ]
}
```

- `id`：管道内**临时字符串 id**，仅用于 `prerequisites` 引用；落库后以 DB 主键为准（API 返回主键 int 列表）
- `prerequisites`：引用同一 journey 内其他 kp 的临时 id；未知 / 重复 id → 落库 400 → 任务 error → 可重试
- 章节 ≥ 1 章、每章 ≥ 1 知识点；依赖边允许跨章节
- 07 扩展位：来源引用（`sources`）等字段将作为 v1 追加字段，不破坏结构
- 校验实现：`app/charplot/services.py::validate_graph`（Django 侧权威校验）

## 2. 任务与 SSE（FastAPI `/ai/*`）

- **任务类型**（HASH 字段 `task_type`，`GET /ai/tasks/{id}` 返回）：
  - `pipeline`：知识管道，阶段 `parsing → analyzing → searching → deconstructing → done / error`，progress 单调递增（15 / 35 / 60 / 90 / 100）
  - `level-generation`（Issue 08）：出题任务，阶段 `preparing → generating → saving → done / error`（10 / 60 / 90 / 100），事件名统一 `pipeline-progress`（DESIGN §4.2：不同任务类型不同 stage 列表）
- `GET /ai/tasks/{id}` → `{task_id, status: running|done|error, stage, progress, error_message?, task_type}`；任务不存在（过期 / 服务重启）→ 404
- `GET /ai/tasks/{id}/events`（SSE）：
  - event 名 `pipeline-progress`，data `{task_id, stage, progress, message}`
  - 每帧带 `id: <递增序号>`（Redis LIST 下标）；断线重连客户端带 `Last-Event-ID`，服务端从增量续推，不丢事件
  - 终端事件（done / error）后流结束；客户端应主动关闭 EventSource
- **失败语义**：落库写自动重试 1 次（transient 5xx / 连接错误）；任务级失败 → `journey.status=failed`（pipeline）/ 关卡 `questions_status=failed`（level-generation，前端「生成失败 · 重试」），前端重试 = 重新 POST 对应端点（同一 journey_id）
- 任务持久化明确不做（DESIGN.md §8）：FastAPI 重启丢内存任务 → SSE 404 → 前端兜底「重新生成」；出题任务丢失时关卡 generating 状态超过 10 分钟视为陈旧，可重新抢占

## 3. 内部端点（FastAPI → Django，服务间认证）

- 认证：请求头 `X-Internal-Token` == 环境变量 `CHARPLOT_INTERNAL_TOKEN`（Django 侧与 FastAPI 侧 .env 同值）；未配置 → 拒绝（fail closed）
- `POST /api/charplot/journeys/{id}/graph/` `{task_id, graph}` → 200 `{status: "ready"}`；契约校验失败 → 400 `{detail: 中文}`
- `POST /api/charplot/journeys/{id}/status/` `{task_id, status: "failed", error_message}` → 200 `{status: "failed"}`
- **幂等**：图谱落库先删后建（事务内），重复调用不产生重复行；重试只会发生在 failed 旅程（无答题数据，Attempt / Level 是 Issue 05 产物）

### 3.1 出题内部端点（Issue 08，均 X-Internal-Token 认证）

- `POST /api/charplot/journeys/{id}/level-generation/` `{task_id, level_seq}` → 抢占 + 出题输入：
  - `200 {claimed: true, input: {journey_id, level_id, level_seq, level_type, difficulty, question_count, new_count, kp, chapter, kp_infos, review_questions}}`
  - `200 {claimed: false, reason: "ready"|"generating", task_id?}`（幂等跳过；generating 超 10 分钟视为陈旧可重新抢占）
  - `review_questions` 为间隔复习题（易错分 × 时间衰减 Top 20%），**含完整答案**，仅内部传递（答案永不直达前端）；复习题固定置于落库题目末尾
- `POST /api/charplot/journeys/{id}/level-generation/questions/` `{task_id, level_seq, questions: [...]}` → `200 {status: "ready"}`；逐题校验失败 → 400 `{detail: 中文}`；有 Attempt 的关卡 update-in-place（保历史），无 Attempt delete+create
- `POST /api/charplot/journeys/{id}/level-generation/failed/` `{task_id, level_seq, error_message}` → `200 {status: "failed"}`（关卡 `questions_status=failed`，前端可重试）

## 4. 旅程状态与列表

- `charplot_journey.status`：`generating → ready / failed`；`cleared` 布尔标识已通关（本票恒 False，Issue 05 通关结算时置 True）
- 列表 `GET /api/charplot/journeys/` → `{journeys: [{id, title, input_type, status, cleared, chapter_count, kp_count, created_at}]}`；**分组在前端**（进行中 = `!cleared`，已通关 = `cleared`）
- 详情 `GET /api/charplot/journeys/{id}/`：图谱规范化嵌套（chapters → knowledge_points，`prerequisites` 为 DB 主键 int 列表）；`graph` 快照存库但 API 不返回（权威 = 规范化表）

## 5. 边界与后续 issue 扩展位

- file 输入：本票仅保存文件不解析（`source_file` 落盘 `app/charplot/uploads/`）；07 需经 Django 内部端点取文件内容（本票不实现）
- 08：题目 JSON 契约沿用本票 `version` 机制（只增不改）
- 技能树（Issue 04）/ 关卡（Issue 05）锚定 DB 主键，不消费管道临时 id

## 6. 知识库契约（Issue 09）

> Issue 10（Milvus 真实索引/检索）的对接依据。只增不改，不 bump v1。

### 6.1 表结构与状态机

- `charplot_knowledge_base`：`name`（主题名）/ `description` / `cover`（图片 URL 字符串）/ `status` / `collection_name`（创建时生成 `cp_kb_{id}`，全量重建沿用）/ `latest_task_id` / `error_message`
- `charplot_knowledge_base_document`：`knowledge_base` FK / `title`（文件名）/ `file` / `file_size`（字节）/ `is_deleted`（软删标记）/ `deleted_at` / `created_at`
- 状态机：`draft → indexing → ready`；`failed` 可重试（重新 claim）；`ready` 可全量重建（任何变更触发，Q18b）与手动下线（`offline`）；`offline` 恢复上线回到 `ready`；`offline` 禁止触发索引（需先上线）
- 格式白名单：`pdf / docx / pptx / md / txt / html`（扩展名判定，不信任 MIME）；单文档 ≤ 20MB

### 6.2 管理端点（Django `/api/charplot/`，is_staff，普通用户 403）

- `POST /kb/` `{name, description?, cover?}` → 201 KB（status=draft）
- `GET /kb/` → `{kbs: [...]}`（**双语义**：管理员含全部状态 / 普通用户仅就绪）
- `GET /kb/{id}/` → KB 详情，`documents`（有效）与 `deleted_documents`（软删，回收区）分组
- `POST /kb/{id}/documents/`（multipart，字段 `files` 多文件）→ 201 `{documents: [...]}`；任一文件非法 → 整批 400 零落库（all-or-nothing）
- `DELETE /kb/documents/{id}/` → 204 软删（可恢复，磁盘文件保留）
- `POST /kb/documents/{id}/restore/` → 200 恢复
- `POST /kb/{id}/offline/` / `POST /kb/{id}/online/` → 200 KB；非法流转 → 400 中文 detail

### 6.3 用户端点

- `GET /topics/`（AllowAny，游客可浏览）→ `{topics: [{id, name, description, cover}]}`，**仅就绪知识库**（draft/indexing/failed/offline 不可见）
- 触发索引：**前端直调 `POST /ai/kb/index`**（DESIGN §4.1 的 Django 端 `POST /api/kb/{id}/reindex` 由该路径实现——与 Issue 08 出题触发同构，仓库无 Django→FastAPI 反向调用先例，不引入反向 URL 配置）

### 6.4 索引内部端点（FastAPI → Django，X-Internal-Token 认证）

- `POST /api/charplot/kb/{id}/index-claim/` `{task_id}` → 抢占 + 索引输入：
  - `200 {claimed: true, documents: [{id, title, filename, file_size, extension}]}`——仅**有效**文档（is_deleted=False，按 id 排序）；`extension`（去点小写）供 Issue 10 解析器按格式选型
  - `200 {claimed: false, reason: "indexing"|"offline"|"no_documents", task_id?}`（幂等跳过；indexing 超过 `KB_INDEX_STALE_MINUTES`（10 分钟）视为陈旧可重新抢占；无有效文档拒绝——防止"就绪但零内容"）
- `POST /api/charplot/kb/{id}/index-save/` `{task_id}` → `200 {status: "ready"}`（kb → ready，清空 error_message）
- `POST /api/charplot/kb/{id}/index-failed/` `{task_id, error_message}` → `200 {status: "failed"}`（kb → failed，管理页「失败 · 重试」）

### 6.5 索引任务（FastAPI `/ai/kb/index`）

- `POST /ai/kb/index` `{kb_id}` → `{task_id}`；`GET /ai/tasks/{id}` 返回 `task_type: "kb-index"`
- 阶段与进度：`parsing(15)` → 每文档交替 `chunking → embedding`（进度 40→85 逐文档单调递增, 流水线语义）→ `indexing(90) → done(100)`；事件名统一 `pipeline-progress`（DESIGN §4.2：不同任务类型不同 stage 列表）
- 失败语义：任务级失败 → `mark_kb_index_failed`（kb → failed），前端「重试」= 重新 POST `/ai/kb/index`
- **真实索引（Issue 10 替换 stub）**：文档二进制经 §6.6 content 端点获取 → `pipeline/parsers` 解析（pdf/docx/pptx/md/txt/html）→ `rag/chunking` 按类型调优切分（md/txt 500/50、html 800/80、pdf/docx/pptx 600/60，metadata 保留 doc_id/title/filename/chunk_index/valid）→ `rag/embeddings` 抽象（默认 bge-m3 本地模型，可切换）→ Milvus **drop+create 全量重建**（软删物理剔除）+ 批量入库

### 6.6 软删与检索过滤（Issue 10 已实现）

- 软删文档检索立即不命中：Django `is_deleted` 标记 + Milvus 向量 metadata `valid` 有效标记，检索时 filter 排除（Q18c）；重建（全量）时物理剔除
- **软删立即生效机制**：检索时（`/ai/kb/search` / KbSource）实时查询 `GET /api/charplot/kb/{id}/deleted-doc-ids/`（内部端点 → `{deleted_doc_ids: [...]}`），构造 Milvus filter `valid == true and doc_id not in [...]` 排除；恢复的文档自动从集合移除 → 重新命中（无需等重建）
- 文档内容获取（索引解析器输入）：`GET /api/charplot/kb/documents/{id}/content/`（内部端点，返回 `{filename, content_base64}`，与 §5 `journey content` 同构；软删文档同样可读，是否索引由 claim 的有效文档清单决定）

### 6.7 检索任务（FastAPI `/ai/kb/search`，Issue 10）

- `POST /ai/kb/search` `{kb_id, query, top_k?}` → `{chunks: [{doc_id, title, filename, chunk_index, content, score}]}`——**片段检索不是答案**（QA.md Q7），生成由调用方 LLM 完成
- 全链路：query rewriting（LLM 改写，失败降级原 query）→ 稠密+稀疏混合检索（`WeightedRanker` 融合，filter 软删排除）→ rerank（必配，默认本地 bge-reranker-v2-m3，配置留空降级保持召回顺序）→ Top-K（默认 `CHARPLOT_RERANK_TOP_K=5`，召回 `CHARPLOT_RETRIEVE_TOP_K=20`）
- 供管道 A 解构 / C 出题（Issue 11）与调试调用；KbSource 实现同链路（pipeline/sources/kb_source.py）
