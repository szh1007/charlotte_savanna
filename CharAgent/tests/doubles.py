"""CharAgent 测试共享替身 (doubles): 时钟 / 睡眠 / 随机源 / 假 Redis / 事件收集.

替身清单 (不标个数 —— 数与代码同步是平白多一处会漂的地方): FakeClock /
RecordingSleep / FixedRandom 服务 #61 的确定性 (时间与随机性可注入) ·
FakeRedisClient 服务 checkpoint 的 Redis 实现 · EventCollector 服务事件流断言与
快照 (#4/#63) · PendingAwareDatabase 服务挂起-恢复那条路 (#25: 在假库之上补一条
「按未决筛」的语义, 框架与业务两边的用例共用) · `hang_forever` 服务超时那条路
(#15: 永不返回的协程载体, 执行层与 loop 层两条用例共用) · `no_backup_endpoint`
服务「默认不碰真的备份端点」(#14: 框架与业务两个套件共用一份, 见它的 docstring).

**假库本体 (`FakeRecordSession` / `FakeRecordDatabase` 与两个造行的) 自 issue 41 起
住在 `CharAgent/db/testing.py`** —— 业务侧的离线跑分器也要一份记录层, 而它不该去
import 框架的测试结构. 本文件把它们原样转发 (老用例的 `from doubles import ...`
一行都不用改), 与 `BrokenRecordDatabase` / `PendingAwareDatabase` 摆在一起.

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

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from CharAgent.db.entities import ToolCall, ToolCallStatus
from CharAgent.db.errors import DataStoreError
from CharAgent.db.testing import (
    FakeRecordDatabase,
    FakeRecordSession,
    record_message,
    record_thread,
)
from CharAgent.stream.utils.types import EventType, StreamEvent

# 转发出来的那四个 (见模块 docstring): 本文件只拿前两个当父类, 后两个一个都不直接
# 用 —— 列出来是让 ruff 的 F401 认账, 说的正是「它们是有意再导出的」.
__all__ = [
    "BrokenRecordDatabase",
    "EventCollector",
    "FakeClock",
    "FakeRecordDatabase",
    "FakeRecordSession",
    "FakeRedisClient",
    "FixedRandom",
    "PendingAwareDatabase",
    "PendingAwareSession",
    "RecordingSleep",
    "hang_forever",
    "record_message",
    "record_thread",
]


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

# 假库本体 (`FakeRecordSession` / `FakeRecordDatabase` / 两个造行的) 自 issue 41
# 起住在包里 (`CharAgent/db/testing.py`): 业务侧的离线跑分器也要一份记录层, 而
# 它不该去 import 框架的**测试**结构. 这里转发它们 —— 一是下面两个子类要继承
# (子类留在本文件: 它们补的是「假库按设计不做的那条过滤」, 断的仍不是 SQL), 二是
# 老用例一直从 `doubles` 取 (`from doubles import ...`), 搬一次家犯不上让十几处
# import 跟着改.


class BrokenRecordDatabase:
    """连不上的假库: 一进事务就抛 (模拟数据库不可用 / 没配).

    给「读不到记录表会怎样」那几条用例用: 写那条路的降级有自己的一批用例, 这个
    管的是**读**那条路 (两条只读路由该回一个干净的 503, 而不是未处理的异常).
    """

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeRecordSession]:
        raise DataStoreError("库连不上 (测试里故意造的)")
        yield  # pragma: no cover - 走不到, 只为让这个函数是个生成器


class PendingAwareSession(FakeRecordSession):
    """假会话 + 一件它按设计不做的事: **按「未决」筛**那一条查询.

    框架的判据是 SQL 里的 `status = 'needs_approval' AND approved_at IS NULL`
    (还要 join 回会话), 而假库**不过滤** (见上面 `FakeRecordSession`: 过滤是 SQL
    的事, 由 pg_db 用例守). 离线用例要验的却是「闸门跟着状态走」—— 那两列在
    Python 侧补上, 于是「挂起时拦住、批完放开」这一串能离线跑通.

    **真 SQL (含 join 回会话、含别人的会话取不走) 由 pg_db 用例守**
    (`test_server_approval_db.py`).

    **为什么搬进 doubles** (issue 35): 现在有两个消费方 —— 框架的 HITL 用例与业务
    的代付用例 (业务那条要恢复端点真的能查到挂起那一行). 放这儿与 `EventCollector`
    同源: 断的不是 SQL, 而 SQL 那半由 pg_db 用例拿真库验.
    """

    def scalars(self, statement: Any) -> list[Any]:
        """工具调用那张表只交**未决**的行出去 (语义见类 docstring)."""
        rows = super().scalars(statement)
        if statement.column_descriptions[0]["entity"] is not ToolCall:
            return rows
        return [
            row
            for row in rows
            if row.status == ToolCallStatus.NEEDS_APPROVAL.value
            and row.approved_at is None
        ]


class PendingAwareDatabase(FakeRecordDatabase):
    """把上面那个会话装进一个假库 (要验挂起-恢复的用例统一用它)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.session = PendingAwareSession(self)


async def hang_forever() -> str:
    """永不返回的协程载体 (#15): 等一个永远没人 set 的事件.

    「工具卡住」的最小形态 —— 只有超时能把它收掉, 所以执行层 (execute_tool) 与
    loop 层 (整轮会不会被拖死) 两条用例都用它. 两处各抄一份的话, 「永不返回」
    这件事就有了两个要同步的实现 (与 EventCollector 同一个理由).
    """
    await asyncio.Event().wait()
    return "到不了这里"


@pytest.fixture(autouse=True)
def no_backup_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认不给模型装配备份端点 (CLOSEAI_*): 用例不该因为本机 .env 而改变装配.

    为什么放在这里 (而不是各 conftest 各抄一份): 框架与业务两个套件都要它, 而两份
    抄本迟早在「哪些变量」上漂 —— 与假库 (`CharAgent/db/testing.py`) 上浮到包里
    是同一条理由. 两边的 conftest 各 import 一次, autouse 在各自的目录里生效.

    为什么非要有这道夹子: 装配代码读的**就是**环境变量 (`build_model` ->
    `fallback_model_from_env`), 而两个套件里都有几步会把根 .env 读进来 (CLI 入口的
    `load_root_env` / PG 那几个夹具的按需读取) —— 那之后「本机 .env 里配了备份」会
    悄悄改变每一条用例的装配结果, 甚至让本该离线的一条去调真的另一家端点.

    两手都做, 因为泄漏有两条路: 删环境变量管住**测试进程启动时**就带着的那份
    (`pytest -m pg` 之后 .env 进了 os.environ), 换掉工厂管住**用例自己**在读 .env
    之后再装配 (CLI 那条路就是如此 —— 只删环境变量拦不住它).

    换工厂用的是本仓已有的做法 (见 test_client_app.py: 只换最外层的生产工厂, 被测
    代码一行不改). 要验「配了备份会怎样」的用例自己 `monkeypatch.setattr` / 传
    `env=` 调 `fallback_model_from_env` —— 本夹子之后生效的才说了算.
    """
    for name in ("CLOSEAI_API_KEY", "CLOSEAI_BASE_URL", "CLOSEAI_CHAT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    from CharAgent.client import app as client_app

    monkeypatch.setattr(client_app, "fallback_model_from_env", lambda *a, **k: None)
