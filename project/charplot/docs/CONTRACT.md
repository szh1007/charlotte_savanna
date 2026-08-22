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

- 阶段：`parsing → analyzing → searching → deconstructing → done / error`，progress 单调递增（15 / 35 / 60 / 90 / 100）
- `GET /ai/tasks/{id}` → `{task_id, status: running|done|error, stage, progress, error_message?}`；任务不存在（过期 / 服务重启）→ 404
- `GET /ai/tasks/{id}/events`（SSE）：
  - event 名 `pipeline-progress`，data `{task_id, stage, progress, message}`
  - 每帧带 `id: <递增序号>`（Redis LIST 下标）；断线重连客户端带 `Last-Event-ID`，服务端从增量续推，不丢事件
  - 终端事件（done / error）后流结束；客户端应主动关闭 EventSource
- **失败语义**：落库写自动重试 1 次（transient 5xx / 连接错误）；任务级失败 → `journey.status=failed`，前端重试 = 重新 `POST /ai/pipeline`（同一 journey_id）
- 任务持久化明确不做（DESIGN.md §8）：FastAPI 重启丢内存任务 → SSE 404 → 前端兜底「重新生成」

## 3. 内部端点（FastAPI → Django，服务间认证）

- 认证：请求头 `X-Internal-Token` == 环境变量 `CHARPLOT_INTERNAL_TOKEN`（Django 侧与 FastAPI 侧 .env 同值）；未配置 → 拒绝（fail closed）
- `POST /api/charplot/journeys/{id}/graph/` `{task_id, graph}` → 200 `{status: "ready"}`；契约校验失败 → 400 `{detail: 中文}`
- `POST /api/charplot/journeys/{id}/status/` `{task_id, status: "failed", error_message}` → 200 `{status: "failed"}`
- **幂等**：图谱落库先删后建（事务内），重复调用不产生重复行；重试只会发生在 failed 旅程（无答题数据，Attempt / Level 是 Issue 05 产物）

## 4. 旅程状态与列表

- `charplot_journey.status`：`generating → ready / failed`；`cleared` 布尔标识已通关（本票恒 False，Issue 05 通关结算时置 True）
- 列表 `GET /api/charplot/journeys/` → `{journeys: [{id, title, input_type, status, cleared, chapter_count, kp_count, created_at}]}`；**分组在前端**（进行中 = `!cleared`，已通关 = `cleared`）
- 详情 `GET /api/charplot/journeys/{id}/`：图谱规范化嵌套（chapters → knowledge_points，`prerequisites` 为 DB 主键 int 列表）；`graph` 快照存库但 API 不返回（权威 = 规范化表）

## 5. 边界与后续 issue 扩展位

- file 输入：本票仅保存文件不解析（`source_file` 落盘 `app/charplot/uploads/`）；07 需经 Django 内部端点取文件内容（本票不实现）
- 08：题目 JSON 契约沿用本票 `version` 机制（只增不改）
- 技能树（Issue 04）/ 关卡（Issue 05）锚定 DB 主键，不消费管道临时 id
