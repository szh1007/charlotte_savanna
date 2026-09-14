"""仓储的公共零件: 「谁能干活」的协议 + 所有仓储的基类.

一句话理解: 仓储 (Repository) 是**取数据的柜员** —— 上层说「给我这个会话最近的
消息」, 柜员去库里取回来, 上层不必知道库里怎么摆的. 本文件规定柜员要有哪些本事
(协议) 以及所有柜员共用的那点家当 (基类).

为什么叫仓储而不叫 DAO: 「仓储」强调的是**面向业务概念的取数口** (给我一个会话 /
给我这次运行的工具调用), 而不是「一张表一个 CRUD 工具」—— 后者的接口形状由表
决定, 前者由**用的人要什么**决定.

两个零件:
- `Database` 协议 —— 仓储需要的**最小**数据库能力 (就是「开一次事务」这一件事).
  写成协议而不是直接依赖 `PgDatabase` 类, 是为了让用例能塞一个替身进来, 也让
  「换个库」时不必动仓储 (只要新库也能开事务).
- `PgRepository` 基类 —— 存一个 database 引用, 并提供 `_session()` 便捷入口.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from sqlalchemy.orm import Session

from CharAgent.db.database import PgDatabase


class Database(Protocol):
    """仓储需要的数据库能力: 能开一次事务 (就这一件事)."""

    def connect(self) -> AbstractAsyncContextManager[Session]:
        """开一次事务 (正常退出提交, 抛异常回滚), 交出会话来跑 SQL."""
        ...


class PgRepository:
    """所有仓储的基类: 记住「用哪个库」, 并提供开事务的便捷写法.

    Args:
        database: 数据库入口 (默认 `PgDatabase()`, 从环境变量读连接串).
    """

    def __init__(self, database: Database | None = None) -> None:
        self._db: Database = database if database is not None else PgDatabase()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Session]:
        """开一次事务 (转发给 database, 让子类里少写一层).

        用法::

            async with self._session() as session:
                session.execute(...)        # 退出时自动提交
        """
        async with self._db.connect() as session:
            yield session
