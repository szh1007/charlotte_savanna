# 03 — PostgreSQL 引擎

**What to build:** PostgreSQL 异步引擎，供 PostgresSaver（LangGraph Agent 会话）使用。

**Blocked by:** 01 — 项目骨架搭建

**Status:** ready-for-agent

- [ ] 创建 `miniblog/core/pg_database.py`：`create_async_engine`（asyncpg）+ `async_sessionmaker`
- [ ] 验证连接：启动时 `SELECT 1` 成功
- [ ] 确认数据库 `PG_DB_NAME_FASTAPI=miniblog` 已存在
