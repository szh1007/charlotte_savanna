"""会话登记表 + 事件路由: HTTP 层最容易被做错的一页.

一句话理解: 会话**按 thread_id 长驻** (不是一次请求一个), 同一会话同一时刻只
准有一次运行; 而事件的出口在会话构造时就定死了, 所以要有一道岔口把事件转给
「当前这次运行」.

两个要命的性质 (想清楚这两条, 本文件的形状就是唯一解):

1. **会话必须长驻.** 对话历史在会话对象的**内存**里 (`client/session.py` 的
   `_history`), 构造时**不从快照恢复**, 而且 `aclose()` 会连模型与存储一起关掉.
   于是「每个请求新建一个会话」这条看起来最自然的路是错的: 第二轮就看不到第一轮
   说过什么. 会话按 thread_id 活着, 框架只管登记与取出.

2. **同一会话不能并发.** `ChatSession` 没有锁, `_history` 是可变共享状态 ——
   两次 `ask()` 交错不是「慢一点」, 是历史真的错了 (与 DESIGN #20「同一 thread
   不并发写」同一条). 本层用登记表拦下并发, 而不是指望调用方自觉.

      HTTP 请求 A ─┐
                   ├─ acquire(同一个 thread_id) ─> A 拿到, B 被拒 (409 会话忙)
      HTTP 请求 B ─┘

**谁建谁关**: 会话由业务的 SessionProvider 建 (框架调它一次), 它持有的模型与
存储通常也是**业务进程级**的 (见 SessionProvider 的 docstring) —— 所以框架
**从不调用会话的 `aclose()`**: 那是业务的资源, 由业务在进程退出时自己收 (框架
关掉一个共享的模型连接池, 会顺手把别的会话一起弄坏). 框架自己建的只有登记项与
运行任务, 它们随进程结束一起消失.

**空闲淘汰** (ticket 17 补上): 会话聊完就长驻内存, 条目数 = 用户数 —— 持久化与
水合落地之后, 「重启不丢」就换成了「不重启就一直涨」. 于是登记表按**空闲时长**
淘汰 (`DEFAULT_IDLE_TTL_SECONDS`, 默认半小时): `acquire` / `entry` / `release`
碰到它就刷新 `last_used`, `acquire` 顺手清一遍超时的条目.

**可以淘汰的前提正是水合**: 登记项里那点东西 (会话对象 + 内存里的消息历史) 只是
**缓存** —— 真相在快照 (接着跑) 与记录表 (给人看) 里. 下一次 `acquire` 会请业务
重新装配一个会话, 而它在第一次提问前会把历史读回来 (见 `client/session.py` 的
`_hydrate_once`). 没有水合这一手, 淘汰等于**静默丢历史**, 那就不能做 —— 这两件事
是一起成立的.

两条纪律: **正在跑的会话绝不淘汰** (busy 集合是现成的判据: 它正干着活, 记忆被丢
就是真的丢了); **淘汰只丢登记项, 不关会话** (`aclose()` 会把进程级共享的模型与
存储一起关掉, 见上面的「谁建谁关」).

**单进程**: 会话表与运行表都在进程内 (`_entries` / `_busy` / `RunRegistry`),
部署必须单进程 (一个 worker). 多 worker 下同一个 thread_id 的两个请求落到不同
进程 —— 各有各的登记表, 并发拦截当场失效, 而且两边各建一个会话同时写同一段
快照 (读回来的历史是混的). 跨进程的会话登记与互斥是另一层东西 (DESIGN #20 的
升级路径: 进程内队列 → 分布式锁); `retry/idempotency.py` 的幂等 store 是同一个
前提 —— 框架里「进程内状态」出现的地方都默认单进程.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING

from CharAgent.agent import RunContext
from CharAgent.client.session import ChatSession
from CharAgent.server.utils.errors import ServerError, ThreadBusyError
from CharAgent.server.utils.types import SessionProvider
from CharAgent.stream import StreamEvent

if TYPE_CHECKING:  # 只为类型标注: 运行时本文件不认识 RunStream, 只调它的 push
    from CharAgent.server.runs import RunStream

# 空闲多久算「可以丢掉了」(默认半小时): 演示与真实客服场景里, 隔半小时没人说话
# 的会话多半不会再聊; 真接着聊也只是重新水合一次 (见模块 docstring 那条前提).
# 值可调 (SessionRegistry 的构造参数) —— 测试要的不是「半小时」这个数, 而是
# 「超过 TTL 的会被清、正在跑的不会」这两条性质.
DEFAULT_IDLE_TTL_SECONDS = 1800.0


class EventRouter:
    """每个会话一个: 把事件转给「当前这次运行」的流 (运行之间的岔口).

    为什么需要它: 事件的出口 (EventSink) 在**会话构造时**就定死了 —— ChatSession
    把它交给 AgentLoop, 之后一辈子不变. 而一个会话要连续服务很多次运行, 每次
    运行各有自己的队列表 (各推各的客户端). 于是会话永久持有同一个 router, 运行
    开始前 `bind(本次运行的流)`, 结束后 `unbind()`.

    它是可调用的 (EventSink 协议) 且**同步**: 往里塞都是内存队列的非阻塞写,
    不该让跑循环的模型调用等一个网络. 没绑定当前运行时事件直接丢弃 —— 两次运行
    之间不会有事件, 真出现丢弃说明有运行在收尾时还在说话, 丢比串台好.
    """

    def __init__(self) -> None:
        self._current: RunStream | None = None

    @property
    def bound(self) -> bool:
        """当前有没有运行接了这条路由 (排查用: 真跑起来时该是 True)."""
        return self._current is not None

    def bind(self, stream: RunStream) -> None:
        """把路由接到这次运行的流上 (运行开始前调)."""
        self._current = stream

    def unbind(self, stream: RunStream) -> None:
        """断开路由 —— 只断「还是我接的那一条」.

        带 stream 参数而不是无条件清空: 迟到的解绑 (上一轮运行收尾慢了一拍)
        不该把下一轮已经接好的路由拆掉.
        """
        if self._current is stream:
            self._current = None

    def __call__(self, event: StreamEvent) -> None:
        """EventSink 协议实现 (同步): 转给当前运行; 没人接就丢."""
        if self._current is not None:
            self._current.push(event)


def _require_same_thread(context: RunContext, session: ChatSession) -> None:
    """校验装配出来的会话确实属于这次运行的会话编号 (不一致就别放行).

    为什么要拦: 登记表按 `RunContext.thread_id` 分区, 而快照按
    `ChatSession.thread_id` 分区 (agent/provider.py 已写明两者必须同一个值).
    业务若把自己的会话装到别的编号上, 两个分区就对不上了 —— 表现是两个不同的
    对话**共用一份快照**, 读回来的历史是别人的 (多用户产品里这是最不该发生的一类
    错). 它不会有任何报错, 只会在某天被人发现"怎么串了", 所以在这里当场拦住.

    抛的是一条明确的 500 (配置/装配错误, 该留 traceback 给自己看), 而不是
    401/503 那类「业务能自己决定话术」的失败 —— 这属于接线 bug.
    """
    if session.thread_id != context.thread_id:
        raise ServerError(
            f"装配出来的会话属于 {session.thread_id!r}, 而这次运行的会话编号是"
            f" {context.thread_id!r}: 快照分区与会话分区必须同一个值"
            " (见 agent/provider.py 对 RunContext 的说明)",
            code="session_thread_mismatch",
            status_code=500,
        )


@dataclass(slots=True)
class SessionEntry:
    """一个会话的登记项: 会话本身 + 它的常驻事件路由 + 最后一回用它是什么时候.

    attributes:
        session: 业务装好的会话 (框架只调它的 ask).
        router: 本会话的事件路由 (每次运行绑一次, 见 EventRouter).
        last_used: 最后一次碰它的时刻 (`monotonic()` 的读数, 越大越新) ——
            空闲淘汰按它比. **不用墙上时钟**: 那个会跳 (对时 / 夏令时), 拿它算
            时长会算出负数. 这个值不进任何响应, 只是内存里的一个刻度.
    """

    session: ChatSession
    router: EventRouter
    last_used: float = field(default_factory=monotonic)


class SessionRegistry:
    """按 thread_id 登记会话: get-or-create + 同一会话不并发.

    并发拦截用「集合 + 同步检查」而不是 asyncio.Lock: 标记必须在**路由函数里**
    就落下 (那时还没开始跑, 才能把一个真实的 409 回给客户端), 而放开要等这次
    运行结束 —— 一次占用跨越「路由返回」与「流跑完」两个阶段, 用 Lock 反而要
    手工配 acquire/release. 集合的检查与写入都是同步的, 单事件循环里没有竞争.
    """

    def __init__(
        self,
        sessions: SessionProvider,
        *,
        idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS,
    ) -> None:
        """
        Args:
            sessions: 第二个插座 (会话装配).
            idle_ttl_seconds: 空闲多久就把登记项丢掉 (秒). 默认半小时; 测试用
                小值 (0 = 每次 acquire 都当它已经空闲, 于是每回都重新装配).
        """
        self._provider = sessions
        self._entries: dict[str, SessionEntry] = {}
        self._busy: set[str] = set()
        self._idle_ttl = idle_ttl_seconds

    async def acquire(self, context: RunContext) -> SessionEntry:
        """占住这个会话并取出它 (第一次见到这个会话编号时, 请业务建一个).

        Args:
            context: 业务解析出来的运行上下文 (thread_id 决定算哪段会话).

        Returns:
            SessionEntry: 这个会话的登记项 (会话 + 事件路由).

        Raises:
            ThreadBusyError: 同一 thread_id 已有一次运行在跑 (框架翻成 409).
            ServerError: 业务装配出来的会话不属于这个会话编号 (框架翻成 500 ——
                `RunContext.thread_id` 与 `ChatSession.thread_id` 必须是同一个值,
                见 agent/provider.py 的说明).
            ServerConfigError: 业务建会话时发现配置不对 (由业务抛出, 框架翻成 503).
        """
        thread_id = context.thread_id
        if thread_id in self._busy:
            raise ThreadBusyError(
                f"会话 {thread_id!r} 已有一次运行在进行中 —— 同一会话不并发写"
                " (DESIGN #20); 请等上一次结束, 或另起一段会话"
            )
        # 顺手清一遍空闲的 (每次 acquire 一次, 不另起后台任务: 登记表小、清理是
        # O(条目数) 的字典遍历, 而多一个常驻任务就多一处要收尾的东西).
        # 放在占住**之前**: 那些超时的条目正是要在这时候让位的
        self.evict_idle()
        self._busy.add(thread_id)
        try:
            entry = self._entries.get(thread_id)
            if entry is None:
                # 建会话要在占住之后: 两个并发请求同时发现「没有这个会话」会各建
                # 一个, 后写的那个把先写的覆盖掉 —— 第一段历史当场丢失.
                router = EventRouter()
                session = await self._provider.provide(context, event_sink=router)
                _require_same_thread(context, session)
                entry = SessionEntry(session=session, router=router)
                self._entries[thread_id] = entry
            entry.last_used = monotonic()
            return entry
        except BaseException:
            # 建会话失败或被取消: 不能把「占住」留着 (留着等于这段会话永远 409)
            self._busy.discard(thread_id)
            raise

    def release(self, thread_id: str) -> None:
        """放开这个会话 (一次运行结束时由框架调用, 与 acquire 成对).

        重复调用是安全的 (幂等): 收尾路径可能不止一条, 谁先到都行.

        顺手刷新 `last_used`: 一次跑了二十分钟的运行结束时, 「空闲」应当从**这
        一刻**算起 —— 不然它一放开就可能立刻被判为超时 (占住期间虽然不会淘汰,
        但计时一直在走).
        """
        self._busy.discard(thread_id)
        entry = self._entries.get(thread_id)
        if entry is not None:
            entry.last_used = monotonic()

    def entry(self, thread_id: str) -> SessionEntry | None:
        """查一个已登记的会话 (没有就是 None) —— 排查、读历史与测试用.

        这是一次**只读查**, 但它会刷新 `last_used` 并顺手清掉超时的条目: 「有人
        刚刚看过这段会话」本身就是一次使用, 而清理是纯粹的缓存动作 (不建会话,
        更不产生运行 —— 那条纪律在 `entry` 上已经立过了).
        """
        self.evict_idle()
        entry = self._entries.get(thread_id)
        if entry is not None:
            entry.last_used = monotonic()
        return entry

    def evict_idle(self) -> tuple[str, ...]:
        """把空闲太久的登记项丢掉 (丢的是内存里那份缓存, 见模块 docstring).

        Returns:
            tuple[str, ...]: 这次淘汰掉的会话编号 (排查与用例用).

        Note:
            **只丢登记项**: 既不关会话 (`aclose()` 会连进程级共享的模型与存储
            一起关掉), 也不动快照与记录 (那是真相所在). 正在跑的 (busy) 一律跳过.
        """
        if not self._entries:
            return ()
        now = monotonic()
        stale = tuple(
            thread_id
            for thread_id, entry in self._entries.items()
            if thread_id not in self._busy and now - entry.last_used >= self._idle_ttl
        )
        for thread_id in stale:
            self._entries.pop(thread_id, None)
        return stale

    @property
    def busy_threads(self) -> frozenset[str]:
        """正在跑的会话编号 (测试断言「收尾真的放开了」用)."""
        return frozenset(self._busy)

    @property
    def thread_ids(self) -> tuple[str, ...]:
        """已登记的会话编号 (登记顺序) —— 测试与排查用."""
        return tuple(self._entries)
