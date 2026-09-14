"""Postgres 版快照存储: 一帧一行, 全历史都在, 能回溯 (ADR-0002 的第三种语义).

一句话理解: 把快照记在**账本**上 —— 每存一帧就多一行, 谁都不擦掉. 于是它能做到
另两个实现做不到的事:

- **翻历史**: 把这个会话的每一帧按时间翻出来 (`list_history`)
- **按编号回到过去 (time-travel)**: 挑一帧老快照恢复, 新产生的那几帧会把
  `parent_id` 指向那帧老快照 —— 于是历史长成一棵树, 「从哪个时刻分出去的」一眼
  就能看出来 (#5)
- **强一致**: 一条 INSERT 落库就是落了, 不是「可能还在路上」

存储形态 (02-data-model.md §3): 一张 `charagent_checkpoints` 表 (表名带前缀的理由
见 utils/ddl.py —— 与 langgraph-checkpoint-postgres 的 `checkpoints` 撞过名),
身份字段 (会话 / 轮次 / 编号 / 时刻) 各占一列 (能建索引、能按条件查), 进度与观察值
各占一个 JSON 列 —— 内部形状随版本变, 用 JSON 存, 加字段不必改表, 且
「恢复要用的」与「给人看的」能分开查.

三个工程取舍 (写下来免得后来的人以为是漏了):

1. **同步驱动 + 线程池, 不用 psycopg 的异步连接**
   用 psycopg 的 AsyncConnection 看着更「异步」, 但它在 Windows 上跑不起来:
   默认的 Proactor 事件循环与它不兼容 (报错原文让换 SelectorEventLoop). 本项目
   在 Windows 上开发与演示, 不能把「换事件循环」这种前置条件塞给使用方. 于是
   改用**同步连接 + `asyncio.to_thread`**: 接口仍是 async (不阻塞事件循环),
   实现里那次阻塞调用挪到工作线程执行. Django 的 async ORM 走的就是这条路
   (sync_to_async), 是异步框架里用同步驱动的标准做法.
2. **连接只有一条, 用锁排队**: P0 就一条连接, 一把 asyncio 锁把存取排成队
   (连接不能同时跑两条语句). 连接池属工程化底座 (P2-10), 现在不为用不上的并发量
   提前铺开 —— 真要高并发时换 psycopg_pool, 协议不变.
3. **autocommit=True**: 每条语句自成一个事务. 存快照本来就是一条 INSERT (不需要
   跨语句的原子性), 打开它顺便避免「忘了 commit, 看起来存了其实没存」这种坑.

建表语句在 utils/ddl.py: 首次写入时幂等建表 (让本包在 P0 能独立跑起来), 同一份
SQL issue 08 的 alembic 迁移会复用.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from CharAgent.checkpoint.serialization import DEFAULT_CODEC, CheckpointCodec
from CharAgent.checkpoint.utils.ddl import (
    CHECKPOINTS_DDL,
    CHECKPOINTS_INDEX_DDL,
    CHECKPOINTS_TABLE,
    CHECKPOINTS_UPGRADE_DDL,
)
from CharAgent.checkpoint.utils.errors import (
    CheckpointConfigError,
    CheckpointStorageError,
)
from CharAgent.checkpoint.utils.types import (
    Checkpoint,
    CheckpointCapabilities,
)

# 查询与写入用的列清单 (显式列举, 不用 SELECT *: 表将来加列时读取的字段集不变)
_COLUMNS = (
    "checkpoint_id, thread_id, run_id, turn_number, schema_version, "
    "state, metadata, parent_id, created_at"
)
_INSERT_SQL = f"""
INSERT INTO {CHECKPOINTS_TABLE} ({_COLUMNS})
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (checkpoint_id) DO NOTHING
"""
# 最新一帧: 时刻最大的一条; checkpoint_id 作第二排序键, 保证同一时刻存入的两帧
# 也有确定顺序 (否则翻历史的结果可能在不同数据库上不一样)
_SELECT_LATEST_SQL = f"""
SELECT {_COLUMNS} FROM {CHECKPOINTS_TABLE}
WHERE thread_id = %s
ORDER BY created_at DESC, checkpoint_id DESC
LIMIT 1
"""
_SELECT_BY_ID_SQL = (
    f"SELECT {_COLUMNS} FROM {CHECKPOINTS_TABLE} WHERE checkpoint_id = %s"
)
_SELECT_HISTORY_SQL = f"""
SELECT {_COLUMNS} FROM {CHECKPOINTS_TABLE}
WHERE thread_id = %s
ORDER BY created_at ASC, checkpoint_id ASC
"""
_SELECT_HISTORY_LIMIT_SQL = f"""
SELECT {_COLUMNS} FROM {CHECKPOINTS_TABLE}
WHERE thread_id = %s
ORDER BY created_at DESC, checkpoint_id DESC
LIMIT %s
"""

_ResultT = TypeVar("_ResultT")


class PostgresCheckpointSaver:
    """把快照存进 Postgres 一张表 (强一致 + 全历史 + time-travel 的底座).

    连接是**懒建立**的: 构造时不连库, 第一次存取才连 —— 于是「造 saver」这个
    动作不需要数据库在线 (配置对不对, 等真用的时候报错也不迟).
    """

    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection: psycopg.Connection | None = None,
        codec: CheckpointCodec | None = None,
    ) -> None:
        """
        Args:
            dsn: Postgres 连接串 (如 "postgresql://user:pwd@127.0.0.1:5432/db");
                用 checkpoint/config.py 的工厂从环境变量拼更省事.
            connection: 注入的已有**同步**连接 (测试 / 复用连接池的连接); 给了它
                就不看 dsn, 且**由调用方负责关闭**, 并保证它开了 autocommit.
            codec: 序列化编解码器, 默认出厂那一个 (DEFAULT_CODEC).

        Raises:
            CheckpointConfigError: dsn 与 connection 都没给.
        """
        if dsn is None and connection is None:
            raise CheckpointConfigError(
                "PostgresCheckpointSaver 需要 dsn (或注入 connection)"
            )
        self._dsn = dsn
        self._codec = codec if codec is not None else DEFAULT_CODEC
        self._owns_connection = connection is None
        self._connection = connection
        # 连接不能同时跑两条语句: 用锁把存取排成队 (见模块 docstring 取舍 2)
        self._lock = asyncio.Lock()
        self._schema_ready = False

    @property
    def capabilities(self) -> CheckpointCapabilities:
        """Postgres 版: 有全历史, 不会过期 (行一直躺在表里, 除非主动清理)."""
        return CheckpointCapabilities(history=True, ttl=False)

    async def ensure_schema(self) -> None:
        """建表建索引 (幂等: 已存在就不动).

        首次写入会自动调一次, 让本包能独立跑起来; 正式上线时表结构由 issue 08 的
        alembic 迁移管 —— 「自动建表」只保证**表在**, 不等于 schema 版本受管, 两者
        不冲突 (用的是 utils/ddl.py 里同一份 SQL).
        """
        await self._call(f"建 {CHECKPOINTS_TABLE} 表", lambda _connection: None)

    async def save(self, checkpoint: Checkpoint) -> None:
        """存一帧 (追加一行; 同编号重复保存 = 什么都不做, 幂等)."""

        def insert(connection: psycopg.Connection) -> None:
            # 进度与观察值各进一个 JSON 列: 编码后只剩 JSON 原生类型 (里面
            # datetime 之类的值已经打上行李牌, 见 serialization.py)
            body = self._codec.encode_body(checkpoint)
            with connection.cursor() as cursor:
                cursor.execute(
                    _INSERT_SQL,
                    (
                        checkpoint.checkpoint_id,
                        checkpoint.thread_id,
                        checkpoint.run_id,
                        checkpoint.turn_number,
                        checkpoint.schema_version,
                        Jsonb(body["state"]),
                        Jsonb(body["metadata"]),
                        checkpoint.parent_id,
                        checkpoint.created_at,
                    ),
                )

        await self._call(f"存快照 (checkpoint_id={checkpoint.checkpoint_id})", insert)

    async def load_latest(self, thread_id: str) -> Checkpoint | None:
        """取该会话最新一帧 (没有则 None)."""
        row = await self._fetch_one(_SELECT_LATEST_SQL, (thread_id,), "查最新快照")
        return self._row_to_checkpoint(row) if row is not None else None

    async def load(
        self, checkpoint_id: str, *, thread_id: str | None = None
    ) -> Checkpoint | None:
        """按编号取一帧 (time-travel 的入口; 没有则 None).

        thread_id 是给「按会话分区存放的实现」(Redis 的流式历史) 用的提示参数:
        本表有全局主键 checkpoint_id, 按编号直查即可, 所以这个参数**传了也忽略**.
        """
        row = await self._fetch_one(_SELECT_BY_ID_SQL, (checkpoint_id,), "按编号查快照")
        return self._row_to_checkpoint(row) if row is not None else None

    async def list_history(
        self, thread_id: str, *, limit: int | None = None
    ) -> list[Checkpoint]:
        """按时间从早到晚列出该会话的快照 (limit 取最近 N 帧, 顺序不变).

        分支 (time-travel 分出去的新线) 也在同一个列表里 —— 谁连着谁看 parent_id.
        """
        if limit is not None and limit <= 0:
            return []
        if limit is None:
            rows = await self._fetch_all(
                _SELECT_HISTORY_SQL, (thread_id,), "查快照历史"
            )
        else:
            # 「最近 N 帧」在库里倒着取最快, 取回来再翻正 (返回顺序仍是早 -> 晚)
            newest_first = await self._fetch_all(
                _SELECT_HISTORY_LIMIT_SQL, (thread_id, limit), "查最近若干快照"
            )
            rows = list(reversed(newest_first))
        return [self._row_to_checkpoint(row) for row in rows]

    async def aclose(self) -> None:
        """关掉自己建的连接 (外部注入的那个不动).

        也走那把锁: 关连接要等手里的操作做完 (否则可能把正在用的连接从底下抽走).
        """
        async with self._lock:
            if self._owns_connection and self._connection is not None:
                await asyncio.to_thread(self._connection.close)
                self._connection = None
                self._schema_ready = False

    # ------------------------------------------------------------------
    # 内部: 把同步的数据库活儿挪到线程里做
    # ------------------------------------------------------------------

    async def _call(
        self, action: str, work: Callable[[psycopg.Connection], _ResultT]
    ) -> _ResultT:
        """执行一段数据库操作: 排队 → 线程里跑 → 出错包装成统一异常.

        三层各管一件事:
        - `self._lock`: 同一时刻只有一个操作在跑 (一条连接不能并发用)
        - `asyncio.to_thread`: 真正干活的是**同步**驱动 (见模块 docstring 取舍 1),
          放到工作线程里执行, 事件循环该干嘛干嘛
        - 异常包装: 底层 psycopg 的报错换成 CheckpointStorageError 并带上「在
          干什么」(action), 排查时不用去猜是哪一步炸的

        Args:
            action: 出错信息里说明「在干什么」的短语.
            work: 拿到可用连接后要执行的同步函数.

        Returns:
            _ResultT: work 的返回值.

        Raises:
            CheckpointStorageError: 连接失败 / 建表失败 / 语句失败.
        """
        async with self._lock:
            try:
                return await asyncio.to_thread(self._work, action, work)
            except psycopg.Error as exc:
                raise CheckpointStorageError(f"{action}失败: {exc}") from exc

    def _work(
        self, action: str, work: Callable[[psycopg.Connection], _ResultT]
    ) -> _ResultT:
        """在工作线程里执行: 备好连接 → 备好表 → 干活 (调用方必须已持锁).

        放在线程里而不是 async 方法里, 是因为「建立连接」本身也是阻塞操作 ——
        连上数据库可能要几百毫秒, 不该卡住事件循环.
        """
        connection = self._connection
        if connection is None or connection.closed:
            connection = psycopg.connect(self._dsn, autocommit=True)
            self._connection = connection
            # 新连接的库里可能还没有表, 下面重新确认一次
            self._schema_ready = False
        if not self._schema_ready:
            with connection.cursor() as cursor:
                cursor.execute(CHECKPOINTS_DDL)
                # 建表语句里的 IF NOT EXISTS 管不了「已经存在的表」—— 更早
                # 版本建的表缺 metadata 列, 用一条幂等的 ALTER 补上 (见 ddl.py)
                cursor.execute(CHECKPOINTS_UPGRADE_DDL)
                cursor.execute(CHECKPOINTS_INDEX_DDL)
            self._schema_ready = True
        return work(connection)

    async def _fetch_one(
        self, sql: str, params: tuple[Any, ...], action: str
    ) -> dict[str, Any] | None:
        """查一行 (按列名取值)."""

        def query(connection: psycopg.Connection) -> dict[str, Any] | None:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()

        return await self._call(action, query)

    async def _fetch_all(
        self, sql: str, params: tuple[Any, ...], action: str
    ) -> list[dict[str, Any]]:
        """查多行 (同上)."""

        def query(connection: psycopg.Connection) -> list[dict[str, Any]]:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()

        return await self._call(action, query)

    def _row_to_checkpoint(self, row: dict[str, Any]) -> Checkpoint:
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
            run_id=row["run_id"],
            turn_number=row["turn_number"],
            state=state,
            metadata=metadata,
            parent_id=row["parent_id"],
            created_at=row["created_at"],
        )
