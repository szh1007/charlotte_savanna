"""Postgres 版快照存储: 一帧一行, 全历史都在, 能回溯.

一句话理解: 把快照记在**账本**上 —— 每存一帧就多一行, 谁都不擦掉. 于是它能做到
另两个实现做不到的事:

- **翻历史**: 把这个会话的每一帧按时间翻出来 (`list_history`)
- **按编号回到过去 (time-travel)**: 挑一帧老快照恢复, 新产生的那几帧会把
  `parent_id` 指向那帧老快照 —— 于是历史长成一棵树, 「从哪个时刻分出去的」一眼
  就能看出来 (#5)
- **强一致**: 一条 INSERT 落库就是落了, 不是「可能还在路上」

表结构在 `db/schema.py` (那张 `charagent_checkpoints` 表) —— 表名带前缀的
理由见那里的模块 docstring (与 langgraph-checkpoint-postgres 的 `checkpoints` 撞
过一次名). 身份字段 (会话 / 轮次 / 编号 / 时刻) 各占一列 (能建索引、能按条件
查), 进度与观察值各占一个 JSON 列 —— 内部形状随版本变, 用 JSON 存, 加字段不必
改表, 且「恢复要用的」与「给人看的」能分开查.

三件与别人不一样的地方 (写下来免得后来的人以为是漏了):

1. **同步驱动 + 线程池, 不用 psycopg 的异步连接**
   psycopg 的 AsyncConnection 在 Windows 默认的 ProactorEventLoop 上跑不起来
   (报错原文让换 SelectorEventLoop; 2026-09-14 用 SQLAlchemy 的
   `create_async_engine` 复测, 同样的错). 本项目在 Windows 上开发与演示, 不能把
   「换事件循环」这种前置条件塞给使用方. 于是走**同步引擎 + `asyncio.to_thread`**
   (这次统一交给 `db/database.py` 的 `PgDatabase`, 快照存储与仓储共用同一套
   连接池与事务语义). Django 的 async ORM (sync_to_async) 走的就是这条路.

2. **一处定义, 不是两份**: 这张表的表定义只写在 `db/schema.py` 里. 本模块运行时
   建表用它, alembic 的 `--autogenerate` 也拿它当「代码侧该长什么样」的基准 ——
   早期是一份手写 SQL 加一份 alembic, 现在统一到 SQLAlchemy 的 Table.
   注意**已发布的迁移脚本是冻结的历史, 刻意不 import 它** (理由见那个脚本的模块
   docstring): 迁移一旦落地就随代码变的话, 版本号就没有意义了.

3. **一次查询 = 一次事务**: 存取都走 `PgDatabase.connect()` (退出自动提交, 出错
   自动回滚). 存快照本来就是一条 INSERT (不需要跨语句的原子性), 但显式的事务
   边界让「存到一半失败」不会留下半截数据.

**schema 检查只做一次**: `ensure_schema` 建完表后在内存里记一笔 (`_schema_ready`),
之后不再重复问库 —— 每次存取都去问一次「表在不在」是白花的往返.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import URL, Select, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine, RowMapping

from CharAgent.checkpoint.serialization import DEFAULT_CODEC, CheckpointCodec
from CharAgent.checkpoint.utils.errors import CheckpointConfigError
from CharAgent.checkpoint.utils.types import (
    Checkpoint,
    CheckpointCapabilities,
)
from CharAgent.db.database import PgDatabase
from CharAgent.db.schema import checkpoints, runs, threads


class PostgresCheckpointSaver:
    """把快照存进 Postgres 一张表 (强一致 + 全历史 + time-travel 的底座).

    连接是**懒建立**的: 构造时不连库, 第一次存取才连 —— 于是「造 saver」这个
    动作不需要数据库在线 (配置对不对, 等真用的时候报错也不迟).
    """

    def __init__(
        self,
        *,
        dsn: str | URL | None = None,
        engine: Engine | None = None,
        codec: CheckpointCodec | None = None,
    ) -> None:
        """
        Args:
            dsn: Postgres 连接串 —— 文本形式 (`"postgresql+psycopg://..."`) 或
                SQLAlchemy 的 URL 对象都收; 用 checkpoint/config.py 的工厂从环境
                变量拼更省事.
            engine: 注入一个现成的 SQLAlchemy 引擎 (测试 / 复用别人的连接池);
                给了它就不看 dsn, 且**由调用方负责释放** (本 saver 不关它).
            codec: 序列化编解码器, 默认出厂那一个 (DEFAULT_CODEC).

        Raises:
            CheckpointConfigError: dsn 与 engine 都没给.
        """
        if dsn is None and engine is None:
            raise CheckpointConfigError(
                "PostgresCheckpointSaver 需要 dsn (或注入 engine)"
            )
        self._codec = codec if codec is not None else DEFAULT_CODEC
        self._owns_database = engine is None
        # 两条路径共用同一套「怎么开事务」的实现: 注入引擎就把它交给 PgDatabase
        # (它知道注入的引擎不该由自己关), 否则用 dsn 建一个自己的
        self._database = (
            PgDatabase(url=dsn) if engine is None else PgDatabase(engine=engine)
        )
        self._schema_ready = False
        # 建表这件事只该有一个在跑: 并发存取同时首次调用 ensure_schema 时,
        # 两边的 create_all 会撞上 (表已存在). 一把锁把首次建表串起来.
        self._schema_lock = asyncio.Lock()

    @property
    def capabilities(self) -> CheckpointCapabilities:
        """Postgres 版: 有全历史, 不会过期 (行一直躺在表里, 除非主动清理)."""
        return CheckpointCapabilities(history=True, ttl=False)

    async def ensure_schema(self) -> None:
        """建表建索引 (幂等: 已存在就不动; 建过就记一笔, 不再重复问库).

        首次写入会自动调一次, 让本包能独立跑起来; 正式上线时表结构由 alembic
        迁移管 —— 「自动建表」只保证**表在**, 不等于 schema 版本受管. 两者用的是
        `db/schema.py` 里同一份表定义, 不冲突.
        """
        if self._schema_ready:
            return
        async with self._schema_lock:
            # 抢到锁时可能别人已经建好了 (双重检查: 不必再跑一次建表往返)
            if self._schema_ready:
                return
            await self._create_table()
            self._schema_ready = True

    async def save(self, checkpoint: Checkpoint) -> None:
        """存一帧 (追加一行; 同编号重复保存 = 什么都不做, 幂等)."""
        await self.ensure_schema()
        body = self._codec.encode_body(checkpoint)
        statement = (
            pg_insert(checkpoints)
            .values(
                checkpoint_id=checkpoint.checkpoint_id,
                thread_id=checkpoint.thread_id,
                loop_id=checkpoint.loop_id,
                run_id=checkpoint.run_id,
                turn_number=checkpoint.turn_number,
                schema_version=checkpoint.schema_version,
                state=body["state"],
                metadata=body["metadata"],
                parent_id=checkpoint.parent_id,
                created_at=checkpoint.created_at,
            )
            # 同一个编号存第二次 = 空操作 (让「存到一半重试」不会存出两份)
            .on_conflict_do_nothing(index_elements=["checkpoint_id"])
        )
        async with self._database.connect() as session:
            session.execute(statement)

    async def load_latest(self, thread_id: str) -> Checkpoint | None:
        """取该会话最新一帧 (没有则 None)."""
        statement = (
            select(checkpoints)
            .where(checkpoints.c.thread_id == thread_id)
            # 时刻最大的一条; checkpoint_id 作第二排序键, 保证同一时刻存入的
            # 两帧也有确定顺序
            .order_by(
                checkpoints.c.created_at.desc(), checkpoints.c.checkpoint_id.desc()
            )
            .limit(1)
        )
        return await self._one(statement)

    async def load(
        self, checkpoint_id: str, *, thread_id: str | None = None
    ) -> Checkpoint | None:
        """按编号取一帧 (time-travel 的入口; 没有则 None).

        thread_id 是给「按会话分区存放的实现」(Redis 的流式历史) 用的提示参数:
        本表有全局主键 checkpoint_id, 按编号直查即可, 所以这个参数**传了也忽略**.
        """
        statement = select(checkpoints).where(
            checkpoints.c.checkpoint_id == checkpoint_id
        )
        return await self._one(statement)

    async def list_history(
        self, thread_id: str, *, limit: int | None = None
    ) -> list[Checkpoint]:
        """按时间从早到晚列出该会话的快照 (limit 取最近 N 帧, 顺序不变).

        分支 (time-travel 分出去的新线) 也在同一个列表里 —— 谁连着谁看 parent_id.
        """
        if limit is not None and limit <= 0:
            return []
        if limit is None:
            statement = (
                select(checkpoints)
                .where(checkpoints.c.thread_id == thread_id)
                .order_by(
                    checkpoints.c.created_at.asc(), checkpoints.c.checkpoint_id.asc()
                )
            )
            rows = await self._all(statement)
        else:
            # 「最近 N 帧」在库里倒着取最快, 取回来再翻正 (返回顺序仍是早 -> 晚)
            statement = (
                select(checkpoints)
                .where(checkpoints.c.thread_id == thread_id)
                .order_by(
                    checkpoints.c.created_at.desc(), checkpoints.c.checkpoint_id.desc()
                )
                .limit(limit)
            )
            rows = list(reversed(await self._all(statement)))
        return [self._to_checkpoint(row) for row in rows]

    async def delete_thread(self, thread_id: str) -> int:
        """删掉某个会话的全部快照, 返回删了几行.

        **这是 Postgres 实现专有的, 不在 `CheckpointSaver` 协议里** (内存版删不删
        无所谓, Redis 版有自己的键过期) —— 按协议编程的调用方不该依赖它.

        本方法要求**表已存在** (它会先 `ensure_schema`). 想「表还没有时也当作删
        了 0 行」的调用方, 自己接一层 (测试的收尾就是这种场景: 用例可能一帧都没
        存过, 那时表可能还不存在).

        与「会话清理策略」是两回事: 那个要考虑保留期与级联删除 (属后续阶段),
        别拿这个方法当清理入口.
        """
        await self.ensure_schema()
        statement = delete(checkpoints).where(checkpoints.c.thread_id == thread_id)
        async with self._database.connect() as session:
            return int(session.execute(statement).rowcount or 0)

    async def aclose(self) -> None:
        """关掉自己建的连接池 (外部注入的引擎不动).

        与 PgDatabase 的约定一致: 谁建的池子谁负责关 —— 注入引擎的那一方可能还
        在用它干别的活.
        """
        if self._owns_database:
            await self._database.dispose()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _create_table(self) -> None:
        """真的去建表 (注入引擎时走的就是注入的那个).

        建的是一小撮表 (ticket 22 起): `charagent_checkpoints` 的 `run_id` 外键
        指着 `charagent_runs`, 而后者又指着 `charagent_threads` —— 只建帧表的话,
        Postgres 会以「被引用的表不存在」当场拒绝建表. 三张一起建, 依赖顺序交给
        `create_all` (它按外键排; 两表之间的那条环由 `use_alter` 推迟成 ALTER,
        见 `db/schema.py` 的说明).
        """
        await self._database.create_tables([threads, runs, checkpoints])

    async def _one(self, statement: Select) -> Checkpoint | None:
        """查一帧 (没有则 None)."""
        rows = await self._all(statement)
        return self._to_checkpoint(rows[0]) if rows else None

    async def _all(self, statement: Select) -> list[RowMapping]:
        """查若干帧.

        **为什么用 `mappings()` 而不是 `scalars()`**: 这里查的是一张 **Table**
        (Core 风格, 不是 ORM 实体), `select(table)` 的每一行是「一行里的所有列」;
        `scalars()` 只会取出**第一列** (checkpoint_id), 后面 `row.state` 就会炸
        (2026-09-14 实测踩到). `mappings()` 给的是「列名 → 值」的映射, 正是
        `_to_checkpoint` 想要的形状.
        """
        await self.ensure_schema()
        async with self._database.connect() as session:
            return list(session.execute(statement).mappings())

    def _to_checkpoint(self, row: RowMapping) -> Checkpoint:
        """一行记录 -> Checkpoint 对象.

        进度要按**行里那个版本号**翻译 (老行可能是老格式写的); 翻译完升到当前
        版本, 所以建出来的对象里 schema_version 就是当前版本 (字段默认值).
        """
        state, metadata = self._codec.decode_body(
            {"state": row["state"], "metadata": row["metadata"]},
            schema_version=row["schema_version"],
        )
        return Checkpoint(
            checkpoint_id=row["checkpoint_id"],
            thread_id=row["thread_id"],
            loop_id=row["loop_id"],
            turn_number=row["turn_number"],
            state=state,
            metadata=metadata,
            # 两个编号各读各的列 (ticket 22): 老行里那个 `run_id` 列已被迁移改名成
            # `loop_id`, 而新的 `run_id` 列是记录层的外键 (老行为 NULL)
            run_id=row["run_id"],
            parent_id=row["parent_id"],
            created_at=_aware(row["created_at"]),
        )


def _aware(moment: datetime) -> datetime:
    """确保时刻带时区.

    TIMESTAMPTZ 列读回来本来就带时区 (psycopg 会给 UTC); 这一手是防**注入的
    引擎**给出裸值 —— 快照的排序与 time-travel 都靠这个时刻, 裸值会让排序在
    跨时区时悄悄错位.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
