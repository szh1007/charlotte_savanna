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
from mock_llm import MockLLM, text_response

from CharAgent.agent import RunContext
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
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
    """造一个最小上下文 (会话编号是登记表唯一认识的东西; 属主那两个字段登记表不看)."""
    return RunContext(thread_id=thread_id, tenant_id="toy", user_id="u-1")


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


# ---------------------------------------------------------------------------
# 空闲淘汰 (ticket 17): 真相在快照与记录里, 内存只是缓存
# ---------------------------------------------------------------------------


async def test_an_idle_session_is_evicted_and_later_rebuilt() -> None:
    """空闲超时的会话被丢掉; 再 acquire 时**重新装配**一个 (它自己会水合).

    为什么敢丢: 会话对象里那点东西 (内存历史) 只是缓存 —— 真相在快照 (接着跑) 与
    记录表 (给人看) 里, 而新装配出来的会话第一次提问前会把历史读回来 (见
    client/session.py 的 `_hydrate_once`). 没有水合这一手, 淘汰就是静默丢历史.
    """
    provider = FakeSessions()
    registry = SessionRegistry(provider, idle_ttl_seconds=0.05)
    first = await registry.acquire(context())
    registry.release(first.session.thread_id)
    await asyncio.sleep(0.06)

    second = await registry.acquire(context())

    assert second.session is not first.session, "超时的那个该被丢掉"
    assert len(provider.sessions) == 2, "重新装配了一次 (装配处会带上水合)"
    assert registry.thread_ids == ("toy:chat-1",), "登记表里只有新的那个"


async def test_a_session_that_is_still_running_is_never_evicted() -> None:
    """正在跑的 (busy) 一律跳过: 它正干着活, 记忆被丢就是真的丢了."""
    provider = FakeSessions()
    registry = SessionRegistry(provider, idle_ttl_seconds=0.05)
    running = await registry.acquire(context())  # 占着不放 (模拟一次运行中)
    await asyncio.sleep(0.06)  # 空闲计时早就超了, 但它在忙

    assert registry.evict_idle() == (), "正忙的那个不该出现在淘汰名单里"
    assert registry.thread_ids == ("toy:chat-1",)

    registry.release(running.session.thread_id)
    entry = await registry.acquire(context())

    assert entry.session is running.session, "整段运行里它始终是同一个会话"
    assert len(provider.sessions) == 1, "一次都没重新装配过"


async def test_a_fresh_session_survives_the_sweep_while_it_is_idle() -> None:
    """空闲但还没超时的会话留着 (淘汰不比 TTL 更激进)."""
    provider = FakeSessions()
    registry = SessionRegistry(provider, idle_ttl_seconds=30.0)
    first = await registry.acquire(context())
    registry.release(first.session.thread_id)

    second = await registry.acquire(context())

    assert second.session is first.session


async def test_reading_an_entry_refreshes_its_idle_timer() -> None:
    """只读查一次也算「用它了」—— `entry` 顺手刷新, 并清掉超时的那些.

    这也是 `/history` 那条路的顺手好处: 有人来看这段对话, 它就被续了命.
    """
    provider = FakeSessions()
    registry = SessionRegistry(provider, idle_ttl_seconds=0.05)
    first = await registry.acquire(context())
    registry.release(first.session.thread_id)
    await asyncio.sleep(0.06)

    assert registry.entry("toy:chat-1") is None, "超时的那条在只读查里被清掉"

    kept = await registry.acquire(context())
    registry.release(kept.session.thread_id)
    assert registry.entry("toy:chat-1") is kept, "刚用过的当然还在"


async def test_evict_idle_reports_what_it_dropped_and_keeps_the_rest() -> None:
    """`evict_idle` 如实回报丢了哪几段 (排查与用例的取数口)."""
    provider = FakeSessions()
    registry = SessionRegistry(provider, idle_ttl_seconds=0.05)
    left = await registry.acquire(context("toy:left"))
    registry.release(left.session.thread_id)
    right = await registry.acquire(context("toy:right"))
    registry.release(right.session.thread_id)
    await asyncio.sleep(0.06)

    dropped = registry.evict_idle()

    assert set(dropped) == {"toy:left", "toy:right"}
    assert registry.thread_ids == ()
    assert registry.evict_idle() == (), "再清一次什么都没了 (幂等)"


async def test_a_busy_session_is_not_dropped_even_when_it_looks_idle() -> None:
    """正忙的会话不在 `evict_idle` 的名单里 (即使它的空闲计时已经超了)."""
    provider = FakeSessions()
    registry = SessionRegistry(provider, idle_ttl_seconds=0.05)
    await registry.acquire(context())
    await asyncio.sleep(0.06)

    dropped = registry.evict_idle()

    assert dropped == ()
    assert registry.thread_ids == ("toy:chat-1",)


# ---------------------------------------------------------------------------
# 淘汰 + 水合: 两件事一起才成立 (见 sessions.py 的模块 docstring)
# ---------------------------------------------------------------------------


class RealSessions:
    """装配**真会话**的提供者 (共用一份内存快照: 模拟业务那几件进程级资源).

    与 `FakeSessions` 的分工: 那个测登记表本身 (只认 ask 与 thread_id), 这个用来
    走通「淘汰 → 重新装配 → 水合回历史」这条路 —— 它需要真的 ChatSession.
    """

    def __init__(self, saver: InMemoryCheckpointSaver) -> None:
        self._saver = saver
        self.sessions: list[ChatSession] = []
        self.models: list[MockLLM] = []

    async def provide(self, context: RunContext, *, event_sink) -> ChatSession:
        # 每个会话一个新模型: 用例要看的是「第二段会话第一次提问时模型收到了什么」
        model = MockLLM.fixed(text_response("答好了"))
        session = ChatSession(
            model,
            saver=self._saver,
            thread_id=context.thread_id,
            event_sink=event_sink,
        )
        self.sessions.append(session)
        self.models.append(model)
        return session


def seen_by(model: MockLLM) -> list[str]:
    """这个模型第一次被调用时看到的正文 (逐条)."""
    return [str(message.get("content")) for message in model.calls[0]["messages"]]


async def test_an_evicted_session_comes_back_with_its_history() -> None:
    """淘汰之后再 acquire: 重新装配出来的会话把历史**水合回来**.

    这是「可以淘汰」那句话的兑现方式: 登记项只是缓存, 真相在快照里, 而让真相回到
    内存的是会话第一次提问前的那一步水合 (见 client/session.py).
    """
    saver = InMemoryCheckpointSaver()
    provider = RealSessions(saver)
    registry = SessionRegistry(provider, idle_ttl_seconds=0.05)
    first = await registry.acquire(context())
    await first.session.ask("订单到哪了")
    registry.release(first.session.thread_id)
    await asyncio.sleep(0.06)

    second = await registry.acquire(context())
    await second.session.ask("那什么时候能到")

    assert second.session is not first.session, "超时的那个被丢了"
    seen = seen_by(provider.models[1])
    assert "订单到哪了" in seen, "新会话把上一段的提问水合回来了"
    assert "答好了" in seen, "上一段的答复也在"
    assert seen[-1] == "那什么时候能到"
