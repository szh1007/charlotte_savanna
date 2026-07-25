# 01 — 项目骨架搭建

**What to build:** 创建 `/miniblog` 目录结构、FastAPI 入口文件、配置模块、健康检查端点。`

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] 按 PRD 4.14 目录结构创建所有空目录和 `__init__.py`
- [ ] 创建 `miniblog/config.py`：读取全部环境变量（MySQL/PostgreSQL/Redis/Milvus/LLM/SMTP/JWT 等），提供 `Settings` 类
- [ ] 创建 `miniblog/main.py`：FastAPI 实例 + lifespan + 根路由 `/` + `GET /api/health` 返回 `{"code":1,"message":"ok"}`
- [ ] 创建 `miniblog/schemas/response.py`：统一响应格式 `{code, message, data, pagination?}`
- [ ] 创建 `miniblog/core/exceptions.py`：全局异常处理器
- [ ] 同步 `.env` 和 `.env.example`：新增全部 miniblog 环境变量，标注 `# --- miniblog 项目 ---`
- [ ] `uvicorn miniblog.main:app --reload` 可启动，`GET /api/health` 返回 200
