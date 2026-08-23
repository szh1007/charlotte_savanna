# 09 — 知识库管理链路

**Status:** done

**Blocked by:** 01 — 三端骨架与健康检查, 02 — 账号体系与个人主页

**What to build:** 管理员（is_staff）通过自建管理页面预建主题知识库：创建（主题名 / 描述 / 封面图）、上传文档（pdf/docx/pptx/md/txt/html）、文档列表管理、软删除（可恢复）、触发索引任务（本票 stub 索引：状态流转 + 假进度）、状态机（草稿 → 索引中 → 就绪 / 失败可重试 / 下线）。用户端仅可见就绪知识库。

**Acceptance criteria:**
- [x] 仅 is_staff 可操作知识库管理（普通用户访问被拒）
- [x] 创建知识库 → 上传多文档 → 触发索引 → 状态机流转到就绪（stub 进度可见）
- [x] 文档软删除后列表隐藏、可恢复；用户端检索不可命中（10 完成前以接口约定为准）
- [x] 失败状态可重试；下线后用户端不可见
- [x] 用户端主题列表仅展示就绪知识库

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 09；PRD C-1/C-2/C-3/C-4；SPEC §6.1 / §8 / Q18、Q18c

**实现摘要 (2026-08-24, commit `cf99a7a`):**

**Django 侧** (`app/charplot/`)
- 模型：`CharplotKnowledgeBase`（name/description/cover URL/status 状态机/collection_name 预留/latest_task_id/error_message）+ `CharplotKnowledgeBaseDocument`（file/file_size/is_deleted/deleted_at），迁移 `0007`
- 服务层 (`services.py`)：格式白名单校验（pdf/docx/pptx/md/txt/html, ≤20MB）、创建（draft + `cp_kb_{id}` collection）、批量落库（all-or-nothing 事务）、软删/恢复、下线/上线（仅 ready ↔ offline）、`claim_kb_index` 原子抢占（indexing/offline/no_documents 拒绝理由, 10min 陈旧重抢占）、save→ready、mark→failed
- API：管理端点（创建/详情含文档分组/多文件上传/软删/恢复/下线/上线, IsStaff）+ 双语义列表（staff 全部 / 用户仅就绪）+ `GET /topics/`（AllowAny 仅就绪）+ 内部端点三连（index-claim/save/failed, X-Internal-Token）
- 测试 `test_knowledge_base.py` 40 个全过

**FastAPI 侧** (`project/charplot/api/`)
- `POST /ai/kb/index`（task_type=kb-index）：stub 索引任务, per-doc 假进度 `parsing → chunking/embedding 交替（40→85 递增）→ indexing → done`, 事件名统一 `pipeline-progress`；失败 → error + mark_kb_index_failed
- `_init_task` 泛化 entity_type（kb/journey）, hash 增写 entity_id/entity_type（journey_id 键保留兼容）
- 测试 `test_kb_index.py` 5 个全过

**前端** (`project/charplot/frontend/`)
- `KBManage.vue`（/admin/kb, requiresStaff 守卫）：创建表单（含封面 URL 预览位）/ el-table 状态徽章列表 / 详情抽屉（文档上传多选 + 有效/已移除分组 + SSE 五阶段 stepper + 失败重试 + 下线/上线）
- `Home.vue`：主题卡片墙（仅就绪库, 游客可见, 点击直达为 Issue 11）
- `App.vue`：is_staff 显示「知识库管理」导航入口
- client.ts：KB 类型 + 10 个 API 函数（含 requestForm 多文件上传）

**契约**：CONTRACT.md §6 知识库契约（状态机/端点/kb-index 任务/软删与 Issue 10 扩展位: 文档内容端点 `GET /api/charplot/kb/documents/{id}/content/` 为 Issue 10 契约）

**与 DESIGN.md 的偏差**：§4.1 `POST /api/kb/{id}/reindex` 由「前端直调 `POST /ai/kb/index` + Django 内部 claim 端点」实现（与 Issue 08 出题触发同构, 不引入 Django→FastAPI 反向调用）, CONTRACT §6.3 已注明

**端到端验证**：Playwright 手工链路全通过（admin 创建→上传 2 文档→索引→就绪→首页卡片；普通用户访问 /admin/kb 弹回首页；软删/恢复；下线后卡片消失/上线恢复）
