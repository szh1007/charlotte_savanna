# 01 — 三端骨架与健康检查

**Status:** ready-for-agent

**Blocked by:** None — can start immediately

**What to build:** CharPlot 三端骨架可启动并打通最小请求链路：Django 侧 app（模型统一 `charplot_` 前缀、charplot_profile 与 auth_user 一对一关联）、FastAPI 侧服务（预留 AI 管道入口）、Vue 前端工程（含 B 站粉动漫风视觉基座：Element Plus 主题定制 + 设计令牌）。共享 MySQL / Redis 配置就绪，健康检查接口联通。这是所有后续任务的基础，落地数据契约与视觉规范。

**Acceptance criteria:**
- [ ] 三端均可通过启动脚本独立启动，访问健康检查返回正常
- [ ] Django 侧可执行迁移创建 charplot_profile 表（1:1 → auth_user），模型名带 charplot_ 前缀
- [ ] 前端渲染 B 站粉主题基座（#FB7299 主色 + 柔和色系设计令牌），Element Plus 主题生效
- [ ] 最小请求链路：前端 → Django API / FastAPI API 各打通一次
- [ ] 共享 MySQL / Redis 配置从 .env 读取，无硬编码密钥

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 01；SPEC §2 项目约定 / §4 架构；PRD §6 视觉方向
