# 02 — MySQL 引擎 + 公共基类 + Alembic

**What to build:** MySQL 异步数据库引擎、CommonBaseModel、Alembic 迁移初始化。

**Blocked by:** 01 — 项目骨架搭建

**Status:** ready-for-agent

- [ ] 创建 `miniblog/core/database.py`：`create_async_engine`（aiomysql）+ `async_sessionmaker` + `get_db` 依赖注入（参考 `demo/FastAPI/demo3_orm.py`）
- [ ] 创建 `CommonBaseModel(DeclarativeBase)`：含 `id`(int PK)、`created_at`、`updated_at` 字段
- [ ] `alembic init miniblog/alembic`
- [ ] 配置 `alembic/env.py` 指向 `CommonBaseModel.metadata` 和 `async_engine`
- [ ] `alembic revision --autogenerate -m "init"` 生成初始迁移
- [ ] `alembic upgrade head` 执行成功
