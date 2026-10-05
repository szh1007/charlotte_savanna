"""记录层的测试替身: 一个认读写、不过滤不排序的假库 (issue 41).

一句话理解: 记录层那几张表 (`charagent_threads` / `runs` / `messages` /
`tool_calls`) 的 `Database` 在这儿的替身 —— 记录员往里写, 取数口往外读, 全程不碰
Postgres.

**为什么它在 `db/` 包里而不是 `tests/` 里**: 有两个消费方, 而其中一个不是测试 ——
业务侧的离线跑分器 (跑分环境要一份记录层, 却不该为它起一个 Postgres). 假库留在
`tests/` 里的话, 跑分器只能 `from CharAgent.tests.doubles import ...`, 那就把框架的
**内部测试结构**变成了别人的依赖; 提升到包里之后, 框架自己的用例与业务的跑分器用
的是**同一份**, 不会漂.

**为什么不进门面** (`CharAgent/db/__init__.py`): 它是测试替身, 不是库 API —— 与
`config.py` 的两个环境变量名常量同一个道理, 需要的人按完整路径取
(`from CharAgent.db.testing import FakeRecordDatabase`).

**用之前先知道这一条** (`scalars` 的语义): 它把「库里有这些行」照实交回去 —
**既不过滤 `WHERE` 也不排序**, 唯一的例外是消息表那张 (`hidden` 那几条不给).
于是 `list_for_run(run_id)` 在假库上会把**全库**的工具调用行交回来. 要一个子集就
调用方自己筛 (跑分器按 `call.run_id` 筛就是这么做的); 真 SQL 的语义由标记 `pg_db`
的用例拿真库守, 这里不假装会.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from CharAgent.db.entities import Message, Run, Thread, ToolCall

__all__ = [
    "FakeRecordDatabase",
    "FakeRecordSession",
    "record_message",
    "record_thread",
]


class FakeRecordSession:
    """假 SQLAlchemy 会话: 写下来的收进「库」, 查的时候按实体交回去.

    读写两半都在这里, 因为记录这条路本来就两头都走 (记录员先写, 接口再读).
    缺的是**过滤与排序** —— 那是 SQL 的事 (`WHERE hidden IS FALSE` / `ORDER BY
    updated_at DESC` 这些), 由 pg_db 用例拿真库守; 这里给出的是「库里有这些行」
    (为什么这件事要写在模块 docstring 里: 调用方一旦忘了它, 读回来的就是全库).

    模拟了两件事 (都不是 SQL 的完整语义, 只为让被测行为成立):
    - 插入带 `ON CONFLICT DO NOTHING` 时, 主键已经在了就跳过 (记录员从 ticket 27
      起「产生即落库 + 收尾补齐」写同一批行两遍, 幂等全靠它)
    - UPDATE 只认「单表 + 等值条件 + 直接给值」, 复合条件等其余语义由 pg_db 守
    """

    # 表名 → 实体类: 写入那条路只拿得到表名 (语句里来的), 而仓储插入之后会回读
    # 一次 (让库补的默认值出现在返回对象里). 消息与工具调用那两张表也一样收进
    # 「库」—— 于是「记录员写进去 → 接口读回来」这条端到端用例成立
    _TABLES = {
        "charagent_threads": Thread,
        "charagent_runs": Run,
        "charagent_messages": Message,
        "charagent_tool_calls": ToolCall,
    }
    # 实体类 → 主键属性名 (按主键找那一行时用; 工具调用那张是三列主键, 单独处理)
    _KEYS = {Thread: "thread_id", Run: "run_id", Message: "message_id"}
    # 表名 → 主键列 (判「这一行已经在了吗」用 —— 幂等插入按它跳过)
    _PK_COLUMNS = {
        "charagent_threads": ("thread_id",),
        "charagent_runs": ("run_id",),
        "charagent_messages": ("message_id",),
        "charagent_tool_calls": ("run_id", "message_id", "tool_call_id"),
    }

    def __init__(self, database: FakeRecordDatabase) -> None:
        self._db = database
        # 写入的每一行: (表名, 那一行的值). 用例按它断言「写了什么」
        self.rows: list[tuple[str, dict]] = []
        # 刷过 updated_at 的表名 (只有 touch 走这条路)
        self.updates: list[str] = []
        # 查过的语句 (按时间顺序): 用例按它断言「传了什么下去」(身份 / 限额)
        self.queries: list[Any] = []

    # --- 读 (接口那条路) ---

    def scalars(self, statement: Any) -> list[Any]:
        """把「库里有的行」交回去 (按语句查的实体分流).

        这里模拟了**一条**过滤规则: 「会话历史只给可见的行」(`list_conversation`
        的契约). 真过滤在 SQL 的 `WHERE hidden IS FALSE` 里, 由 pg_db 用例拿真库
        守着 —— 这条模拟是为了让「写进去 → 读回来」的端到端用例成立, 不是替代它.
        """
        self.queries.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        rows = list(self._rows_of(entity) or [])
        if entity is Message:
            rows = [row for row in rows if not row.hidden]
        return rows

    def get(self, entity: Any, key: Any) -> Any:
        """按主键取一行 (仓储插入之后会回来读一次).

        假「库」是几个列表, 于是这里扫一遍找主键 —— 真库那边是索引查找, 那是它的
        事 (SQL 由 pg_db 用例守). 工具调用的主键是三列, 于是 `key` 是个元组.
        """
        rows = self._rows_of(entity)
        if rows is None:
            return None
        if entity is ToolCall:
            wanted = dict(
                zip(self._PK_COLUMNS["charagent_tool_calls"], key, strict=True)
            )
            return next(
                (
                    row
                    for row in rows
                    if all(
                        getattr(row, name) == value for name, value in wanted.items()
                    )
                ),
                None,
            )
        attribute = self._KEYS[entity]
        return next(
            (row for row in rows if getattr(row, attribute) == key),
            None,
        )

    def _rows_of(self, entity: Any) -> list[Any] | None:
        """某个实体在假库里的那批行 (认不出来的实体给 None)."""
        return {
            Thread: self._db.threads,
            Run: self._db.runs,
            Message: self._db.messages,
            ToolCall: self._db.tool_calls,
        }.get(entity)

    def expire_all(self) -> None:
        """仓储读前会清一次缓存 —— 假会话没有缓存, 什么都不用做."""

    # --- 写 (记录员那条路) ---

    def execute(self, statement: Any, params: Any = None) -> Any:
        """落一行 (insert) 或改几行 (update), 都收进「库」里."""
        table = statement.table.name
        if statement.is_insert:
            # 批量插入走 `params` (消息那批), 单行插入走语句里带的值
            rows = params if isinstance(params, list) else [statement.compile().params]
            # 带 ON CONFLICT DO NOTHING 的插入是**幂等**的: 主键已经在了就跳过
            # (仓储判它看方言子句在不在, 与真库那边是同一个特征)
            idempotent = getattr(statement, "_post_values_clause", None) is not None
            for row in rows:
                if idempotent and self._exists(table, row):
                    continue
                self._remember(table, dict(row))
            return _Affected(1)
        if statement.is_update:
            changed = self._apply_update(statement)
            self.updates.append(table)
            return _Affected(changed)
        self.updates.append(table)
        return _Affected(1)

    def _exists(self, table: str, row: dict) -> bool:
        """这张表里已经有同主键的一行了吗 (幂等插入按它跳过)."""
        columns = self._PK_COLUMNS.get(table)
        entity = self._TABLES.get(table)
        if columns is None or entity is None:
            return False
        return any(
            all(getattr(existing, name) == row.get(name) for name in columns)
            for existing in self._rows_of(entity) or []
        )

    def _apply_update(self, statement: Any) -> int:
        """把一条 UPDATE 落到假库里匹配的那些行上, 返回改到了几行.

        只认仓储里真实存在的形状: **单表 + 等值条件 + 直接给值**. 真 SQL 的其余语义
        (复合条件 / 表达式 / 子查询) 由 pg_db 用例拿真库守 —— 这里要的只是让「写完
        再读回来」成立 (ticket 22 起 `finish` / `set_title` / `touch` 全是 UPDATE).

        读 `_values` 与 `_where_criteria` 这两个非公开属性: 公开的 `compile().params`
        把 SET 与 WHERE 的绑定参数混在一起 (名字还被加了 `_1` 后缀), 在测试替身里
        照着语句改内存, 比手写一套 SQL 解析诚实.
        """
        entity = self._TABLES[statement.table.name]
        criteria = {}
        for expression in _flatten_and(statement._where_criteria):
            column = getattr(expression.left, "name", None)
            right = getattr(expression, "right", None)
            if column is not None and right is not None:
                criteria[column] = getattr(right, "value", None)
        values = {name: bind.value for name, bind in statement._values.items()}
        changed = 0
        for row in self._rows_of(entity) or []:
            if any(getattr(row, key, None) != value for key, value in criteria.items()):
                continue
            for name, value in values.items():
                setattr(row, name, value)
            changed += 1
        return changed

    @property
    def params(self) -> dict[str, Any]:
        """最近一次查询绑定的参数 (排查「租户 / 属主 / 限额传下去了吗」用)."""
        return dict(self.queries[-1].compile().params)

    def _remember(self, table: str, row: dict) -> None:
        """收下这一行, 并让它在假库里「查得到」(插入后仓储会再读一次)."""
        self.rows.append((table, row))
        entity = self._TABLES.get(table)
        if entity is None:
            return
        rows = self._rows_of(entity)
        assert rows is not None, f"{table} 认得出实体却找不到它的那批行"
        rows.append(entity(**row))


class _Affected:
    """`execute` 的返回值 (只有 touch 会看 rowcount)."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


def _flatten_and(criteria: Sequence[Any]) -> list[Any]:
    """WHERE 里的条件拆成一条条 (把 `and_(...)` 摊平).

    仓储里有两种写法都在用: `.where(c1, c2)` (两条并列) 与 `.where(and_(c1, c2))`
    (`ThreadsRepository._owned` 那种「归属是一件事」的写法). 真 SQL 里两者完全
    等价, 而替身按条件逐条比, 所以要自己摊一下.

    **只摊 AND**: OR / 子查询 / 表达式那些是**语义**上的差别, 仍旧由 pg_db 用例
    拿真库守 (见模块 docstring), 这里不假装会.
    """
    flat: list[Any] = []
    for expression in criteria:
        flat.extend(getattr(expression, "clauses", None) or [expression])
    return flat


class FakeRecordDatabase:
    """假库入口 (`Database` 协议: 能开一次事务就行).

    用法::

        records = FakeRecordDatabase(
            messages=[record_message("user", "订单到哪了")],   # 预置「库里已有的」
            threads=[record_thread("toy:u-9f3a:1", title="订单")],
        )
        app = create_app(..., database=records)          # 读那条路 (历史 / 列表)
        service = build_service(..., database=records)   # 写那条路 (记录员落账)

    写下来的东西也进同一批列表 (于是「跑完一轮再看列表」这种用法成立); 要断言
    「具体写了哪几行」看 `session.rows`.
    """

    def __init__(
        self,
        *,
        messages: Sequence[Any] = (),
        threads: Sequence[Any] = (),
        runs: Sequence[Any] = (),
        tool_calls: Sequence[Any] = (),
    ) -> None:
        self.messages = list(messages)
        self.threads = list(threads)
        self.runs = list(runs)
        self.tool_calls = list(tool_calls)
        self.session = FakeRecordSession(self)

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeRecordSession]:
        yield self.session

    async def dispose(self) -> None:
        """关掉连接池 —— 假库没有池, 所以什么都不做.

        它必须存在: 装配那一层收尾时一律 `await database.dispose()` (`PgDatabase`
        有这个方法), 少了它, 拿假库当 `database` 的一方会在收尾那一刻炸 —— 而且是
        在模型 / 快照 / 客户端**都关掉之后**才炸, 排查时看到的是一个与真因无关的
        `AttributeError`. 空实现比「记得别调收尾」可靠.
        """

    def written_rows_of(self, table: str) -> list[dict]:
        """某个表**这次新插了**哪些行 (按插入顺序) —— 断言「没另建行」这类用.

        与 `rows_of` 的区别: 那个答「库里现在有什么」(含预置的行与随后的 UPDATE),
        这个答「这次写了哪些新行」. 要断「没有多出一行」时只有它能说明问题.
        """
        return [row for name, row in self.session.rows if name == table]

    def rows_of(self, table: str) -> list[dict]:
        """某个表**现在**有哪些行 (按写入顺序) —— 断言「库里成了什么样」用.

        读的是**实体的当前状态**, 不是插入那一刻的参数: 仓储从 ticket 22 起会用
        UPDATE 推进已存在的行 (运行行的终态与 `last_checkpoint_id`、会话行的标题),
        而那些正是用例要断言的「最终写成了什么」. 没有实体映射的表退回插入流水.
        """
        entity = FakeRecordSession._TABLES.get(table)
        if entity is None:
            return [row for name, row in self.session.rows if name == table]
        attribute = {
            Thread: "threads",
            Run: "runs",
            Message: "messages",
            ToolCall: "tool_calls",
        }[entity]
        columns = list(entity.__table__.columns.keys())
        return [
            {name: getattr(row, name) for name in columns}
            for row in getattr(self, attribute)
        ]


def record_message(
    role: str,
    content: str | None,
    *,
    thread_id: str = "toy:u-9f3a:1",
    hidden: bool = False,
) -> Message:
    """造一条记录表的行 (投影只读 role / content, 其余字段够填满 NOT NULL 即可)."""
    return Message(
        message_id=uuid4().hex,
        thread_id=thread_id,
        run_id=None,
        role=role,
        content=content,
        reasoning=None,
        tool_call_ids=[],
        hidden=hidden,
        created_at=datetime.now(UTC),
    )


def record_thread(
    thread_id: str,
    *,
    tenant_id: str = "toy",
    user_id: str = "u-9f3a",
    title: str = "",
    status: str = "active",
    updated_at: datetime | None = None,
    pinned_at: datetime | None = None,
) -> Thread:
    """造一条会话行 (列表那条路要的东西)."""
    moment = datetime.now(UTC) if updated_at is None else updated_at
    return Thread(
        thread_id=thread_id,
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
        status=status,
        created_at=moment,
        updated_at=moment,
        pinned_at=pinned_at,
    )
