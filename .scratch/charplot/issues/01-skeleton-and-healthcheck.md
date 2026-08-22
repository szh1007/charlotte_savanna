# 01 — 三端骨架与健康检查

**Status:** ready-for-human

**Blocked by:** None — can start immediately

**What to build:** CharPlot 三端骨架可启动并打通最小请求链路：Django 侧 app（模型统一 `charplot_` 前缀、charplot_profile 与 auth_user 一对一关联）、FastAPI 侧服务（预留 AI 管道入口）、Vue 前端工程（含 B 站粉动漫风视觉基座：Element Plus 主题定制 + 设计令牌）。共享 MySQL / Redis 配置就绪，健康检查接口联通。这是所有后续任务的基础，落地数据契约与视觉规范。

**Acceptance criteria:**
- [x] 三端均可通过启动脚本独立启动，访问健康检查返回正常
- [x] Django 侧可执行迁移创建 charplot_profile 表（1:1 → auth_user），模型名带 charplot_ 前缀
- [x] 前端渲染 B 站粉主题基座（#FB7299 主色 + 柔和色系设计令牌），Element Plus 主题生效
- [x] 最小请求链路：前端 → Django API / FastAPI API 各打通一次
- [x] 共享 MySQL / Redis 配置从 .env 读取，无硬编码密钥

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 01；SPEC §2 项目约定 / §4 架构；PRD §6 视觉方向

---

## Comments

### 2026-08-22 实施完成（Claude Code）

**交付物：**

| 端 | 路径 | 说明 |
|----|------|------|
| Django | `app/charplot/` | `CharplotProfile`（db_table=`charplot_profile`，1:1 → auth_user，游戏化字段 xp/level/streak/max_streak/hearts/coins）；`GET /api/charplot/health` 探活 MySQL + Redis；迁移 0001 已应用；3 个测试 |
| FastAPI | `project/charplot/api/server.py` | `GET /ai/health` 探活共享 Redis；CORS 允许 3001；`python -m project.charplot.api.server` 启动于 8001 |
| 前端 | `project/charplot/frontend/` | Vue 3 + Vite + TS + Element Plus；`styles/theme.css` 设计令牌（#FB7299 + 奶白/浅紫/淡蓝 + 16px 圆角 + 软阴影）；闯关式三端状态卡（签名元素：关卡编号徽章 + 呼吸状态灯）；vite 代理 /api→8000、/ai→8001 |
| 脚本 | `sh/charplot_backend.sh` / `sh/charplot_frontend.sh` | 独立启动 FastAPI / 前端；Django 用 `manage.py runserver` |
| 配置 | `project/charplot/.env.example`（.env 不提交） | REDIS_URL 从环境读取，无硬编码密钥 |

**验证结果（2026-08-22 实测）：**

- 三端 health 全绿：Django `{status: ok, db: ok, redis: ok}`；FastAPI `{status: ok, redis: ok}`；前端 3001 页面可访问
- 最小请求链路：前端代理 → Django `/api/charplot/health` 与 FastAPI `/ai/health` 均返回 200 ok
- Playwright 页面验证：0 console error；横幅「三端联通 · 骨架可启动」；CSS 变量 `--el-color-primary: #fb7299` 生效；渐变背景 + 16px 圆角渲染正确
- `vue-tsc` 类型检查 + `vite build` 通过；ruff check/format 通过；`manage.py test app.charplot` 3 个测试 OK
- 依赖环境：MySQL（charlotte 库）已建表；Redis 经 Docker 容器（redis:latest, 127.0.0.1:6379）连通

**备注：** Redis 不可达时 health 返回 503 `degraded`（各依赖标记 error），前端状态卡显示琥珀色降级横幅——此行为为预期设计。
