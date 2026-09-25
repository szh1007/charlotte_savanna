"""会话登记表 + 事件路由: HTTP 层最容易被做错的一页.

一句话理解: 会话**按 thread_id 长驻** (不是一次请求一个), 同一会话同一时刻只
准有一次运行**或一次未决挂起**; 而事件的出口在会话构造时就定死了, 所以要有一道
岔口把事件转给「当前这次运行」.

三个要命的性质 (想清楚这三条, 本文件的形状就是唯一解):

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

3. **有未决挂起时也不许发新提问** (issue 34 补的一档). 挂起时那次运行已经收尾
   (登记表放开了), 于是**会话看起来不忙** —— 而用户的新提问会从最新快照起跑,
   那份快照恰好停在「欠着一条要人批的调用」的半路上. 两条路撞在一起的表现是:
   用户刚说的话与那次未决的确认互相搅, 而模型看到的历史里躺着一条它永远不会
   得到结果、也不能重发的调用. 闸门因此比「忙」宽一档, 而且**判据落在库里**
   (`charagent_tool_calls.status = needs_approval AND approved_at IS NULL`):

   - **内存拦不住这件事**: 挂起可能跨进程重启 (用户第二天回来点确认), 而本文件
     自己写着「部署必须单进程、重启后内存集合清空」.
   - 挂起的两条出口 (`resume` 与 `cancel`) **照常放行**: 它们是解决这次挂起的
     动作, 拦它们等于把用户锁在门外 (`acquire` 的 `resuming=True`).

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

三条纪律: **正在跑的会话绝不淘汰** (busy 集合是现成的判据: 它正干着活, 记忆被丢
就是真的丢了); **有待批挂起的会话也不淘汰** (它是「正在干活」的另一形态 —— 会话
没了, 用户回来点确认时那口记忆得重建一次, 而重建要读的水合路径正是上面那条);
**淘汰只丢登记项, 不关会话** (`aclose()` 会把进程级共享的模型与存储一起关掉, 见
上面的「谁建谁关」).

**单进程**: 会话表与运行表都在进程内 (`_entries` / `_busy` / `RunRegistry`),
部署必须单进程 (一个 worker). 多 worker 下同一个 thread_id 的两个请求落到不同
进程 —— 各有各的登记表, 并发拦截当场失效, 而且两边各建一个会话同时写同一段
快照 (读回来的历史是混的). 跨进程的会话登记与互斥是另一层东西 (DESIGN #20 的
升级路径: 进程内队列 → 分布式锁); `retry/idempotency.py` 的幂等 store 是同一个
前提 —— 框架里「进程内状态」出现的地方都默认单进程. 注意**未决挂起这一档是个
例外**: 它的判据是注入进来的一个查询 (装配时给的是 PG), 于是它跨得了重启 ——
这正是它不能只靠内存集合的原因.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING

from CharAgent.agent import RunContext
from CharAgent.client.session import ChatSession
from CharAgent.server.utils.errors import (
    ServerError,
    ThreadBusyError,
    ThreadSuspendedError,
)
from CharAgent.server.utils.types import SessionProvider
from CharAgent.stream import StreamEvent

if TYPE_CHECKING:  # 只为类型标注: 运行时本文件不认识 RunStream, 只调它的 push
    from CharAgent.server.runs import RunStream

# 空闲多久算「可以丢掉了」(默认半小时): 演示与真实客服场景里, 隔半小时没人说话
# 的会话多半不会再聊; 真接着聊也只是重新水合一次 (见模块 docstring 那条前提).
# 值可调 (SessionRegistry 的构造参数) —— 测试要的不是「半小时」这个数, 而是
# 「超过 TTL 的会被清、正在跑的不会」这两条性质.
DEFAULT_IDLE_TTL_SECONDS = 1800.0

# 「这段会话有没有未决挂起」的判据 (注入的异步查询).
#
# 为什么是**注入**而不是本层自己查: 本层不认识库 (它只管登记与路由, 一行 SQL 都
# 不写), 而判据必须落库 —— 挂起可能跨进程重启 (见模块 docstring 第 3 条).
# 装配处 (server/app.py) 有库, 就把那个查询接上; 没有库的装配拿不到判据, 于是
# 这一档闸门**不存在** (那种装配里挂起态本来就没有家 —— ADR-0014 把它的宿主定在
# `charagent_tool_calls` 那一行上).
type SuspendedThreads = Callable[[str], Awaitable[bool]]


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
    """按 thread_id 登记会话: get-or-create + 同一会话不并发 (也不许带病并发).

    并发拦截用「集合 + 同步检查」而不是 asyncio.Lock: 标记必须在**路由函数里**
    就落下 (那时还没开始跑, 才能把一个真实的 409 回给客户端), 而放开要等这次
    运行结束 —— 一次占用跨越「路由返回」与「流跑完」两个阶段, 用 Lock 反而要
    手工配 acquire/release. 集合的检查与写入都是同步的, 单事件循环里没有竞争.

    **未决挂起那一道是异步的** (要问库): 它是另一条判据, 与上面那个内存集合
    并存 —— 集合管「这个进程里正在跑」, 那个查询管「还挂着等人批的」, 而后者
    跨得了重启 (见模块 docstring 第 3 条).
    """

    def __init__(
        self,
        sessions: SessionProvider,
        *,
        idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS,
        suspended: SuspendedThreads | None = None,
    ) -> None:
        """
        Args:
            sessions: 第二个插座 (会话装配).
            idle_ttl_seconds: 空闲多久就把登记项丢掉 (秒). 默认半小时; 测试用
                小值 (0 = 每次 acquire 都当它已经空闲, 于是每回都重新装配).
            suspended: 「这段会话有没有未决挂起」的判据 (一个异步查询); None =
                没有这道闸门 (没配记录层的装配 —— 那种装配里挂起态本来就没有
                家, 见 `SuspendedThreads`).
        """
        self._provider = sessions
        self._entries: dict[str, SessionEntry] = {}
        self._busy: set[str] = set()
        self._idle_ttl = idle_ttl_seconds
        self._suspended = suspended

    async def has_pending_approval(self, thread_id: str) -> bool:
        """这段会话有没有未决挂起 (没注入判据时恒为 False).

        为什么单独一个方法而不是把查询塞进两处: **同一个判据有两个调用方** ——
        `acquire` (收新提问时拦) 与 `evict_idle` (别把等人的会话当空闲丢掉).
        各写一遍的话, 迟早有一处漏了 `approved_at IS NULL` 那一半, 于是「批过了
        的」还被当成挂着.
        """
        if self._suspended is None:
            return False
        return bool(await self._suspended(thread_id))

    async def acquire(
        self, context: RunContext, *, resuming: bool = False
    ) -> SessionEntry:
        """占住这个会话并取出它 (第一次见到这个会话编号时, 请业务建一个).

        Args:
            context: 业务解析出来的运行上下文 (thread_id 决定算哪段会话).
            resuming: 这一次是**去解决**一次挂起 (审批恢复) 还是接新提问. True 时
                两件事一起变:
                ① 跳过「有没有未决挂起」那道闸门 —— 恢复与取消是挂起的两条出口,
                拦它们等于把用户锁在门外;
                ② **丢掉缓存的那个会话对象, 让业务重新装配一次** —— 恢复会随请求
                带一份**一次性载荷** (如支付密码, ADR-0015), 而工具是装配时用闭包
                捕获身份/凭据的, 复用上一段那个会话等于把这份载荷丢掉. 重新装配的
                代价很小 (会话的内存状态本来就从快照来: 这正是 resume 的定义).
                「忙」那道闸门照常生效 (恢复也是一次运行, 不许与别的运行并发).

        Returns:
            SessionEntry: 这个会话的登记项 (会话 + 事件路由).

        Raises:
            ThreadBusyError: 同一 thread_id 已有一次运行在跑 (框架翻成 409).
            ThreadSuspendedError: 这段会话有一次未决挂起而这不是在解决它
                (框架翻成 409, 码不同 —— 前端要能区分「在跑」与「等人」).
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
        if not resuming and await self.has_pending_approval(thread_id):
            # 顺序说明: 「忙」先判 (它更常见, 也更快 —— 不必为一次必然被拒的请求
            # 去查库), 而「挂着」这一条要查库, 于是排在后面
            raise ThreadSuspendedError(
                f"会话 {thread_id!r} 有一次未决的人工确认还没处理: 请先给那次挂起"
                "一个结论 (恢复或取消), 再接着聊 —— 新提问会与那次未决的调用撞在"
                "同一份存档上"
            )
        if resuming:
            # 丢掉旧会话 (不关它: 谁建谁关), 下面就会请业务重新装配一个 —— 于是
            # 这一次装配看得见这次运行带的载荷 (见 Args 的 resuming)
            self._entries.pop(thread_id, None)
        # 顺手清一遍空闲的 (每次 acquire 一次, 不另起后台任务: 登记表小、清理是
        # O(条目数) 的字典遍历, 而多一个常驻任务就多一处要收尾的东西).
        # 放在占住**之前**: 那些超时的条目正是要在这时候让位的
        await self.evict_idle()
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

    def forget(self, thread_id: str) -> bool:
        """把这一段会话从登记表里丢掉 (只丢缓存, 不关会话) —— 明确动作, 不是淘汰.

        与 `evict_idle` 的分工: 那个按空闲时长批量清, 这个是对**某一段**会话的明确
        动作, 用在「库里的事实变了, 而内存里那份会话已经跟它不一致」的场合.

        目前唯一一处: 一次挂起被**取消**之后 (见 `app._cancel_suspension`) —— 会话
        内存里那份历史停在「欠着一条调用的结果」的半路上, 而那条调用永远不会执行
        了. 那种形状直接发给模型会被上游拒掉 (assistant 带 `tool_calls` 后面必须
        跟着 tool 消息), 而它在内存里**看不出来**是坏的: 只有真模型会当场报 400.

        丢掉的代价很小 (与淘汰同一条理由): 下一次 acquire 请业务重新装配, 它在第一次
        提问前从快照水合 —— 那时 `_seal_pending_calls` 会给那条调用补一条「结果未知」
        的回填, 于是发给模型的请求又是配对的.

        Returns:
            bool: 真的丢掉了 True (没有这一段会话时 False, 供用例断言).
        """
        return self._entries.pop(thread_id, None) is not None

    async def entry(self, thread_id: str) -> SessionEntry | None:
        """查一个已登记的会话 (没有就是 None) —— 排查、读历史与测试用.

        这是一次**只读查**, 但它会刷新 `last_used` 并顺手清掉超时的条目: 「有人
        刚刚看过这段会话」本身就是一次使用, 而清理是纯粹的缓存动作 (不建会话,
        更不产生运行 —— 那条纪律在 `entry` 上已经立过了).
        """
        await self.evict_idle()
        entry = self._entries.get(thread_id)
        if entry is not None:
            entry.last_used = monotonic()
        return entry

    async def evict_idle(self) -> tuple[str, ...]:
        """把空闲太久的登记项丢掉 (丢的是内存里那份缓存, 见模块 docstring).

        候选先按「忙不忙 + 闲了多久」筛出来, 再逐条问一句「它有未决挂起吗」——
        顺序有意: 前两个条件是内存里的比较, 后一个要查库, 于是只有**真的空闲到
        该淘汰**的那些才会产生一次查询.

        Returns:
            tuple[str, ...]: 这次淘汰掉的会话编号 (排查与用例用).

        Note:
            **只丢登记项**: 既不关会话 (`aclose()` 会连进程级共享的模型与存储
            一起关掉), 也不动快照与记录 (那是真相所在). 正在跑的 (busy) 与
            **等着人给结论的**一律跳过 —— 后者是「正在干活」的另一形态.
        """
        if not self._entries:
            return ()
        now = monotonic()
        candidates = tuple(
            thread_id
            for thread_id, entry in self._entries.items()
            if thread_id not in self._busy and now - entry.last_used >= self._idle_ttl
        )
        stale: list[str] = []
        for thread_id in candidates:
            if await self.has_pending_approval(thread_id):
                continue
            self._entries.pop(thread_id, None)
            stale.append(thread_id)
        return tuple(stale)

    @property
    def busy_threads(self) -> frozenset[str]:
        """正在跑的会话编号 (测试断言「收尾真的放开了」用)."""
        return frozenset(self._busy)

    @property
    def thread_ids(self) -> tuple[str, ...]:
        """已登记的会话编号 (登记顺序) —— 测试与排查用."""
        return tuple(self._entries)
