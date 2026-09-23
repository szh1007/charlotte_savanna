"""CharAgent 测试共享替身 (doubles): 时钟 / 睡眠 / 随机源 / 假 Redis / 事件收集.

替身清单 (六个): FakeClock / RecordingSleep / FixedRandom 服务 #61 的确定性
(时间与随机性可注入) · FakeRedisClient 服务 checkpoint 的 Redis 实现 ·
EventCollector 服务事件流断言与快照 (#4/#63) · FakeRecordDatabase 服务记录表
那条线 (#17: 记录员写入、接口读回, 三个测试文件共用一份).

与 `tests/helpers.py` 的分工: helpers 放 wire 样本与常量 (「真实响应长什么样」),
本文件放**测试替身** —— 注入被测代码的缝, 让时间与随机性在测试里完全确定
(#61 注入随机 / 时间源), 不真等服务, 也不真等待一秒.

三个「可调用对象」替身 (直接当参数注入, 无需 mock 框架):
- `FakeClock`:   手动推进的固定时钟 (LoopGuard.time_source /
                 RetryPolicy.time_source / 假 Redis 的过期判定)
- `RecordingSleep`: 记录式睡眠 (RetryPolicy.sleep): 记下每次等待时长并推进
                 固定时钟 —— 于是「等待多久」与「过了多久」用同一份状态
- `FixedRandom`: 固定随机源 (RetryPolicy.random_source): 依序吐预设值

两个「当参数注入的假对象」:
- `FakeRedisClient`: Redis 客户端的假身 (checkpoint 的 Redis 实现注入它).
  为什么手写而不引 fakeredis: 本包只用 SET/GET/EXPIRE (latest 模式) 与
  XADD/XRANGE/XREVRANGE/EXPIRE (history 模式的流), 手写替身零依赖且时钟完全
  可控 (TTL 到期不必真等); 与真 Redis 的差异见该类 docstring, 真实行为由标记
  `redis` 的用例 (需本机 Redis) 在本地补验.
- `EventCollector`: 事件收集 sink (AgentLoop.event_sink 注入它);
  事件序列快照与载荷断言的取数口. 它也是可调用的 (__call__ 即 sink 协议),
  只是注入方式与前三者不同 —— 前三者注入策略对象, 它注入事件出口.

既有测试文件里各自的私有收集器 (如 test_loop_events.py 的 `_Collector`) 保持
原样不动 —— 新用例统一用 `EventCollector`, 老文件不为改名而改.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from CharAgent.db.entities import Message, Run, Thread
from CharAgent.db.errors import DataStoreError
from CharAgent.stream.utils.types import EventType, StreamEvent


class FakeClock:
    """固定时钟: 值由测试手动推进 (时间源注入缝, #61 确定性的做法)."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class RecordingSleep:
    """记录式睡眠: 记下等待时长并推进固定时钟 (不真等待).

    attributes:
        delays: 每次睡眠的秒数, 按调用顺序 (退避序列断言的证据).
    """

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.delays: list[float] = []
        self._clock = clock

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self._clock is not None:
            self._clock.now += seconds


class FixedRandom:
    """固定随机源: 依序返回预设值 (用尽后停在最后一个), 使抖动完全确定 (#61)."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> float:
        if not self._values:
            raise AssertionError("FixedRandom 没有预设值, 测试写错了")
        self.calls += 1
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


class FakeRedisClient:
    """Redis 客户端的假身: 只实现 checkpoint 用到的那几条命令.

    支持的命令 (正好是本包用到的):
    - latest 模式: `set(name, value, ex=...)` / `get(name)`
    - history 模式: `xadd(name, fields, maxlen=..., approximate=...)` /
      `xrange(name, "-", "+")` / `xrevrange(name, "+", "-", count=...)`
    - 两档共用: `expire(name, seconds)` / `delete(*names)` / `aclose()` / `ttl(name)`

    与真实 Redis 的差异 (写清楚, 免得把替身当标准):
    - 过期靠**注入的时钟**判定 (读取时发现过期就删), 真实 Redis 是后台主动清理
      (所以真实环境里键可能多活一小会儿, 语义上等价: 过期后读到 None)
    - 流的 id 用「时钟毫秒 + 该键上的序号」生成, 单调递增; XRANGE/XREVRANGE 只
      支持全量范围 ("-" 到 "+") 加 count, **不**实现真 Redis 的 id 区间过滤
      (本包用不到)
    - 不编码文本 (存进去什么就是什么), 也不做类型检查

    时钟可注入是刻意的: TTL 用例要「快进 10 秒」而不是真等 10 秒 (#61 同款思路).

    attributes:
        set_calls: 每次 set 的 (键名, ex 参数) 记录.
        xadd_calls: 每次 xadd 的 (键名, maxlen 参数) 记录.
        expire_calls: 每次 expire 的 (键名, 秒数) 记录.
        closed: 是否被 aclose 关过 (断言「谁建的谁关」).
    """

    def __init__(self, clock: FakeClock | None = None) -> None:
        self._clock = clock if clock is not None else FakeClock()
        # 键 -> (值, 过期时刻); 值是字符串 (latest) 或条目列表 (history 的流)
        self._store: dict[str, tuple[Any, float | None]] = {}
        self._entry_seq = 0
        self.set_calls: list[tuple[str, int | None]] = []
        self.xadd_calls: list[tuple[str, int | None]] = []
        self.expire_calls: list[tuple[str, int]] = []
        self.closed = False

    # ------------------------------------------------------------------
    # 键值 (latest 模式)
    # ------------------------------------------------------------------

    async def set(self, name: str, value: str, *, ex: int | None = None) -> None:
        """写字符串键 (ex 给过期秒数; redis-py 的参数名就叫 ex, 照抄好对齐)."""
        self.set_calls.append((name, ex))
        self._store[name] = (value, self._deadline(ex))

    async def get(self, name: str) -> str | None:
        """读字符串键 (不是字符串键或已过期都返回 None)."""
        found = self._live(name)
        if found is None:
            return None
        value, _ = found
        return value if isinstance(value, str) else None

    # ------------------------------------------------------------------
    # 流 (history 模式)
    # ------------------------------------------------------------------

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        """向流里追加一条 (maxlen 给定时裁掉最老的, 模拟 XADD ... MAXLEN)."""
        self.xadd_calls.append((name, maxlen))
        entries: list[tuple[str, dict[str, str]]] = []
        found = self._live(name)
        if found is not None and isinstance(found[0], list):
            entries = found[0]
        self._entry_seq += 1
        entry_id = f"{int(self._clock.now * 1000)}-{self._entry_seq}"
        entries.append((entry_id, dict(fields)))
        if maxlen is not None and len(entries) > maxlen:
            entries = entries[-maxlen:]
        self._store[name] = (entries, self._existing_deadline(name))
        return entry_id

    async def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        """读流 (从最早到最新; count 取前 N 条)."""
        entries = self._stream(name)
        return list(entries[:count] if count is not None else entries)

    async def xrevrange(
        self,
        name: str,
        max: str = "+",
        min: str = "-",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        """倒着读流 (从最新到最早; count 取前 N 条)."""
        entries = list(reversed(self._stream(name)))
        return list(entries[:count] if count is not None else entries)

    # ------------------------------------------------------------------
    # 通用
    # ------------------------------------------------------------------

    async def expire(self, name: str, seconds: int) -> bool:
        """给键设过期时间 (键不存在返回 False, 与真 Redis 一致)."""
        self.expire_calls.append((name, seconds))
        found = self._live(name)
        if found is None:
            return False
        value, _ = found
        self._store[name] = (value, self._deadline(seconds))
        return True

    async def delete(self, *names: str) -> int:
        """删键 (返回删掉的个数)."""
        removed = 0
        for name in names:
            if self._live(name) is not None:
                del self._store[name]
                removed += 1
        return removed

    async def aclose(self) -> None:
        """假装关连接 (只记一个标记, 供断言)."""
        self.closed = True

    def ttl(self, name: str) -> float:
        """查看剩余存活秒数 (没有这个键 -1; 永不过期 -2) —— 仅排查用."""
        found = self._store.get(name)
        if found is None:
            return -1
        _, deadline = found
        if deadline is None:
            return -2
        return max(0.0, deadline - self._clock.now)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _deadline(self, ex: int | None) -> float | None:
        """按注入的时钟算过期时刻 (None 表示不过期)."""
        return None if ex is None else self._clock.now + ex

    def _existing_deadline(self, name: str) -> float | None:
        """取键上原有的过期时刻 (追加流条目不该把 TTL 抹掉)."""
        found = self._store.get(name)
        return None if found is None else found[1]

    def _live(self, name: str) -> tuple[Any, float | None] | None:
        """取键 (已过期就顺手删掉并返回 None)."""
        found = self._store.get(name)
        if found is None:
            return None
        _, deadline = found
        if deadline is not None and self._clock.now >= deadline:
            del self._store[name]
            return None
        return found

    def _stream(self, name: str) -> list[tuple[str, dict[str, str]]]:
        """取流条目列表 (不是流 / 不存在都当空流)."""
        found = self._live(name)
        if found is None or not isinstance(found[0], list):
            return []
        return found[0]


class EventCollector:
    """事件收集 sink: 把 EventBus 推出来的事件按顺序攒起来 (#4).

    注入方式与 CLI / SSE 完全一样 (`AgentLoop(event_sink=collector)`) —— 同步
    回调, 事件的顺序与编号就是事件流本来的顺序, 不做任何加工.

    attributes:
        events: 收到的事件 (按 seq 顺序).
    """

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    def __call__(self, event: StreamEvent) -> None:
        """EventSink 协议实现 (同步): 收下事件, 不阻塞 run."""
        self.events.append(event)

    @property
    def terminal_types(self) -> list[str]:
        """终局事件类型列表 (应为恰好一个: final 或 error)."""
        return [
            event.type.value
            for event in self.events
            if event.type in (EventType.FINAL, EventType.ERROR)
        ]


# ---------------------------------------------------------------------------
# 记录表那条线的替身 (ticket 17): 一个假库, 读写都认
# ---------------------------------------------------------------------------

# 放进 doubles 的理由与 EventCollector 同源: 三个地方要用它 (db 的记录员用例 /
# server 的历史与列表用例 / 业务的装配用例), 而它要断的都不是 SQL —— SQL 由标记
# pg_db 的用例拿真库验.


class FakeRecordSession:
    """假 SQLAlchemy 会话: 写下来的收进「库」, 查的时候按实体交回去.

    读写两半都在这里, 因为记录这条路本来就两头都走 (记录员先写, 接口再读).
    缺的是**过滤与排序** —— 那是 SQL 的事 (`WHERE hidden IS FALSE` / `ORDER BY
    updated_at DESC` 这些), 由 pg_db 用例拿真库守; 这里给出的是「库里有这些行」.
    """

    # 表名 → 实体类: 写入那条路只拿得到表名 (语句里来的), 而仓储插入之后会回读
    # 一次 (让库补的默认值出现在返回对象里). 消息那张表也一样收进「库」——
    # 于是「记录员写进去 → 接口读回来」这条端到端用例成立
    _TABLES = {
        "charagent_threads": Thread,
        "charagent_runs": Run,
        "charagent_messages": Message,
    }
    # 实体类 → 主键属性名 (按主键找那一行时用)
    _KEYS = {Thread: "thread_id", Run: "run_id", Message: "message_id"}

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
        事 (SQL 由 pg_db 用例守).
        """
        rows = self._rows_of(entity)
        if rows is None:
            return None
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
            for row in rows:
                self._remember(table, dict(row))
            return _Affected(1)
        if statement.is_update:
            changed = self._apply_update(statement)
            self.updates.append(table)
            return _Affected(changed)
        self.updates.append(table)
        return _Affected(1)

    def _apply_update(self, statement: Any) -> int:
        """把一条 UPDATE 落到假库里匹配的那些行上, 返回改到了几行.

        只认仓储里真实存在的形状: **单表 + 等值条件 + 直接给值**. 真 SQL 的其余语义
        (复合条件 / 表达式 / 子查询) 由 pg_db 用例拿真库守 —— 这里要的只是让「写完
        再读回来」成立 (ticket 22 起 `finish` / `set_title` / `touch` 全是 UPDATE).

        读 `_values` 与 `_where_criteria` 这两个非公开属性: 公开的 `compile().params`
        把 SET 与 WHERE 的绑定参数混在一起 (名字还被加了 `_1` 后缀), 在测试替身里
        照着语句改内存, 比手写一套 SQL 解析诚实。
        """
        entity = self._TABLES[statement.table.name]
        criteria = {}
        for expression in statement._where_criteria:
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


class BrokenRecordDatabase:
    """连不上的假库: 一进事务就抛 (模拟数据库不可用 / 没配).

    给「读不到记录表会怎样」那几条用例用: 写那条路的降级有自己的一批用例, 这个
    管的是**读**那条路 (两条只读路由该回一个干净的 503, 而不是未处理的异常).
    """

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeRecordSession]:
        raise DataStoreError("库连不上 (测试里故意造的)")
        yield  # pragma: no cover - 走不到, 只为让这个函数是个生成器


class FakeRecordDatabase:
    """假库入口 (`Database` 协议: 能开一次事务就行).

    用法::

        records = FakeRecordDatabase(
            messages=[record_message("user", "订单到哪了")],   # 预置「库里已有的」
            threads=[record_thread("toy:u-9f3a:1", title="订单")],
        )
        app = create_app(..., database=records)                # 读那条路
        service = MinimallService(..., database=records)       # 写那条路

    写下来的东西也进同一批列表 (于是「跑完一轮再看列表」这种用法成立); 要断言
    「具体写了哪几行」看 `session.rows`.
    """

    def __init__(
        self,
        *,
        messages: Sequence[Any] = (),
        threads: Sequence[Any] = (),
        runs: Sequence[Any] = (),
    ) -> None:
        self.messages = list(messages)
        self.threads = list(threads)
        self.runs = list(runs)
        self.session = FakeRecordSession(self)

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeRecordSession]:
        yield self.session

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
        attribute = {Thread: "threads", Run: "runs", Message: "messages"}[entity]
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
    )
