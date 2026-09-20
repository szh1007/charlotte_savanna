"""server 会话登记表与事件路由: 两条最容易做错的性质 (sessions.py 的守卫).

1. **会话长驻**: 同一个 thread_id 只建一次 (第二次提问拿到的是同一个会话对象),
   不同 thread_id 各是各的.
2. **同一会话不并发**: 第二次 acquire 被拒 (框架翻成 409 的那种拒绝); 收尾放开
   之后又能进.

外加一条不显眼但要命的: 建会话**失败**时不能把「占住」留着 —— 留着等于这段
会话永远 409 (用户怎么说都没用). 所以失败与被取消两条路径都要放开.

会话替身是极小对象 (框架只用它的 ask), 于是这里测的是登记表本身, 不牵扯模型与
存储. 事件路由单独测「事件真的到了当前那次运行的流里」.
"""

from __future__ import annotations

import asyncio

import pytest

from CharAgent.agent import RunContext
from CharAgent.server.runs import RunStream
from CharAgent.server.sessions import EventRouter, SessionRegistry
from CharAgent.server.utils.errors import (
    ServerConfigError,
    ServerError,
    ThreadBusyError,
)
from CharAgent.stream import EventType, StreamEvent


class FakeSession:
    """会话替身: 登记表只用它的 ask 与 thread_id (真的 ChatSession 在 app 用例里)."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.asked: list[str] = []

    async def ask(self, question: str) -> None:
        self.asked.append(question)


class FakeSessions:
    """SessionProvider 替身: 记下每次装配的上下文与事件出口, 按需失败.

    为什么要记 `sinks`: 「框架把哪个出口交给了业务」是接缝上最容易搞错的一件事
    —— 交给别人的出口, 事件就推不到对应客户端的流里, 而这件事在用例里看不见
    (没有任何异常, 只是客户端一直等).
    """

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.contexts: list[RunContext] = []
        self.sinks: list[object] = []
        self.sessions: list[FakeSession] = []
        self._fail = fail

    async def provide(self, context: RunContext, *, event_sink) -> FakeSession:
        self.contexts.append(context)
        self.sinks.append(event_sink)
        if self._fail is not None:
            raise self._fail
        session = FakeSession(context.thread_id)
        self.sessions.append(session)
        return session


def context(thread_id: str = "toy:chat-1") -> RunContext:
    """造一个最小上下文 (会话编号是登记表唯一认识的东西)."""
    return RunContext(thread_id=thread_id)


def queued(stream: RunStream) -> list[StreamEvent]:
    """把流里已排队的事件取出来 (收尾哨兵不算) —— 断言「谁收到了什么」用."""
    items: list[StreamEvent] = []
    while not stream.queue.empty():
        item = stream.queue.get_nowait()
        if item is not None:
            items.append(item)
    return items


async def test_the_same_thread_reuses_one_session() -> None:
    """同一个 thread_id 只建一次会话 —— 对话历史就活在这个对象里."""
    provider = FakeSessions()
    registry = SessionRegistry(provider)

    first = await registry.acquire(context())
    registry.release(first.session.thread_id)
    second = await registry.acquire(context())

    assert first.session is second.session, "第二次提问必须拿到同一个会话"
    assert len(provider.sessions) == 1, "装配只该发生一次"
    assert registry.thread_ids == ("toy:chat-1",)


async def test_different_threads_get_their_own_sessions() -> None:
    """不同会话编号各建各的 (多用户隔离从这一层就分开了)."""
    provider = FakeSessions()
    registry = SessionRegistry(provider)

    left = await registry.acquire(context("toy:left"))
    right = await registry.acquire(context("toy:right"))

    assert left.session is not right.session
    assert len(provider.sessions) == 2


async def test_the_provider_receives_the_session_router_as_the_sink() -> None:
    """交给业务的事件出口, 就是那个会话的常驻路由 (事件由此分到各次运行)."""
    provider = FakeSessions()
    registry = SessionRegistry(provider)

    entry = await registry.acquire(context())

    assert provider.sinks == [entry.router], "业务拿到的必须是这个会话的路由"


async def test_a_carried_router_only_forwards_to_the_bound_run() -> None:
    """路由把事件转给「当前绑定的那次运行」, 解绑之后不再转发."""
    router = EventRouter()
    stream = RunStream("run-1")
    router.bind(stream)

    router(StreamEvent(type=EventType.THINKING, seq=1, data={"message": "在想"}))
    router.unbind(stream)
    router(StreamEvent(type=EventType.FINAL, seq=2, data={"content": "答完了"}))

    assert [event.seq for event in queued(stream)] == [1], (
        "解绑之后的事件不该再进流 (那属于下一次运行)"
    )


async def test_a_late_unbind_does_not_detach_the_next_run() -> None:
    """迟到的解绑不该把下一轮已经接好的路由拆掉 (上一轮收尾慢一拍是常事)."""
    router = EventRouter()
    first, second = RunStream("run-1"), RunStream("run-2")
    router.bind(first)
    router.bind(second)

    router.unbind(first)
    router(StreamEvent(type=EventType.FINAL, seq=1, data={"content": "答完了"}))

    assert second.queue.qsize() == 1, "路由仍然接着第二次运行"


async def test_a_second_question_on_the_same_thread_is_refused() -> None:
    """同一会话不并发: 第二次占位被拒 (不是静默串台).

    这一条是 DESIGN #20「同一 thread 不并发写」在服务层的落点 —— 拒绝是明确的,
    而且**排除了静默损坏历史**这条路 (会话没有锁, 两次 ask 交错就是真的错了).
    """
    provider = FakeSessions()
    registry = SessionRegistry(provider)
    await registry.acquire(context())

    with pytest.raises(ThreadBusyError) as excinfo:
        await registry.acquire(context())

    assert excinfo.value.status_code == 409
    assert "toy:chat-1" in str(excinfo.value), "错误里要说清是哪段会话在忙"
    assert len(provider.sessions) == 1, "被拒的请求不该顺手再建一个会话"


async def test_releasing_lets_the_next_question_in() -> None:
    """放开之后同一会话又能进 (收尾没放开 = 这段会话从此报废)."""
    provider = FakeSessions()
    registry = SessionRegistry(provider)
    entry = await registry.acquire(context())

    registry.release(entry.session.thread_id)
    again = await registry.acquire(context())

    assert again.session is entry.session
    registry.release(entry.session.thread_id)
    assert registry.busy_threads == frozenset()


async def test_a_session_from_another_thread_is_refused() -> None:
    """装配出来的会话装错了分区 → 当场拦住, 不静默放行.

    为什么值得拦: 登记表按上下文的编号分区, 快照按会话自己的编号分区. 两者不一致
    时, 两个不同的对话会**共用一份快照** (读回来的历史是别人的) —— 没有任何报错,
    只在某天被人发现「怎么串了」. 多用户产品里这是最不该发生的一类错.
    """

    class MisplacedSessions(FakeSessions):
        """装错分区的提供者: 交出来的会话属于另一段对话."""

        async def provide(self, context: RunContext, *, event_sink) -> FakeSession:
            return FakeSession("别的对话")

    registry = SessionRegistry(MisplacedSessions())

    with pytest.raises(ServerError) as excinfo:
        await registry.acquire(context())

    assert excinfo.value.code == "session_thread_mismatch"
    assert excinfo.value.status_code == 500, "接线 bug: 该留 traceback 给自己看"
    assert "别的对话" in str(excinfo.value)
    assert registry.busy_threads == frozenset(), "装错了也不能把会话占住"


async def test_a_failed_assembly_does_not_keep_the_thread_claimed() -> None:
    """装配失败要放开占位 —— 否则这段会话永远 409 (用户怎么问都没用)."""
    provider = FakeSessions(fail=ServerConfigError("模型没配"))
    registry = SessionRegistry(provider)

    with pytest.raises(ServerConfigError):
        await registry.acquire(context())

    assert registry.busy_threads == frozenset(), "失败的装配不该把会话占住"
    provider._fail = None  # 配好了再试一次 (同一个登记表)
    entry = await registry.acquire(context())
    assert entry.session.thread_id == "toy:chat-1"


async def test_a_cancelled_assembly_does_not_keep_the_thread_claimed() -> None:
    """装配被打断 (客户端走人) 同样要放开 —— 取消路径最容易被漏掉."""
    gate = asyncio.Event()

    class SlowSessions(FakeSessions):
        async def provide(self, context: RunContext, *, event_sink) -> FakeSession:
            await gate.wait()
            return await super().provide(context, event_sink=event_sink)

    registry = SessionRegistry(SlowSessions())
    pending = asyncio.create_task(registry.acquire(context()))
    await asyncio.sleep(0)  # 让它跑到装配里

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert registry.busy_threads == frozenset()
