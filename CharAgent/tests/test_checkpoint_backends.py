"""跨实现语义对比测试 (同一段经历喂不同存储, 恢复结果一致).

对比的口径是「**恢复出来的东西**一样」, 而不是「存储里长得一样」—— 介质不同,
长相本来就不同 (Redis 是流里的一串 JSON, Postgres 是列 + 两个 JSON 列).

四个实现一起参数化: 内存 / Redis-latest / Redis-history 默认就跑, Postgres 打
`pg` 标记默认排除 (需本机 PG):

    pytest tests/test_checkpoint_backends.py        # 前三个
    pytest -m pg tests/test_checkpoint_backends.py  # 再加真 Postgres

每个用例用自己的会话号 (pg_thread_id), 各组参数互不干扰. 于是「换存储不换行为」
这句话是被真的跑出来的, 不是文档里说说的.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from conftest import postgres_test_dsn
from doubles import FakeRedisClient
from helpers import make_checkpoint
from mock_llm import ScriptedModel, make_tool_call, text_response, tool_call_response

from CharAgent.agent.guard import LoopGuard
from CharAgent.agent.loop import AgentLoop
from CharAgent.checkpoint.base import CheckpointSaver
from CharAgent.checkpoint.memory import InMemoryCheckpointSaver
from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.checkpoint.redis import MODE_LATEST, RedisCheckpointSaver
from CharAgent.checkpoint.utils.errors import CheckpointCapabilityError
from CharAgent.checkpoint.utils.types import CheckpointCapabilities
from CharAgent.model.utils.types import Usage
from CharAgent.tool import Tool, tool

SaverFactory = Callable[[], Awaitable[CheckpointSaver]]


async def memory_saver() -> CheckpointSaver:
    """内存版: 最省事的存储 (也是「协议该有的行为」的基准)."""
    return InMemoryCheckpointSaver()


async def redis_history_saver() -> CheckpointSaver:
    """Redis 流式历史 (默认模式) + 假客户端, 于是这组参数默认不必起 Redis."""
    return RedisCheckpointSaver(client=FakeRedisClient(), key_prefix="test")


async def redis_latest_saver() -> CheckpointSaver:
    """Redis 只留最新一帧 (对齐 langgraph 的 ShallowRedisSaver)."""
    return RedisCheckpointSaver(
        client=FakeRedisClient(), key_prefix="test", mode=MODE_LATEST
    )


async def postgres_saver() -> CheckpointSaver:
    """真 Postgres (本机); 没配连接信息就跳过这一组参数 (不影响另三组)."""
    saver = PostgresCheckpointSaver(dsn=postgres_test_dsn())
    await saver.ensure_schema()
    return saver


# 四组参数: 前三组默认跑, Postgres 需 -m pg
ALL_SAVERS = [
    pytest.param(memory_saver, id="memory"),
    pytest.param(redis_history_saver, id="redis-history"),
    pytest.param(redis_latest_saver, id="redis-latest"),
    pytest.param(postgres_saver, id="postgres", marks=pytest.mark.pg),
]
# 「有历史」的那几个: 翻历史 / 按编号回溯 / 分叉 都该能跑
# (按 ALL_SAVERS 的下标取, 免得靠字符串去猜哪个是 latest)
HISTORY_SAVERS = [ALL_SAVERS[0], ALL_SAVERS[1], ALL_SAVERS[3]]


@pytest.fixture
async def saver(request: pytest.FixtureRequest) -> AsyncIterator[CheckpointSaver]:
    """按参数造一个存储, 用完关掉 (参数是各实现的工厂函数)."""
    factory: SaverFactory = request.param
    created = await factory()
    yield created
    await created.aclose()


def make_counting_tool(result: str = "订单已发货") -> tuple[Tool, list[str]]:
    """造一个「数自己被调了几次」的工具 (判断有没有重复执行要靠调用次数)."""
    calls: list[str] = []

    @tool(name="query_order", description="按订单号查询订单状态")
    def query_order(order_no: str) -> str:
        calls.append(order_no)
        return result

    return query_order, calls


# 「同一段经历」= 一次带工具的问答: 第一轮调工具, 第二轮给答复
SAME_RUN_SCRIPT = [
    tool_call_response(make_tool_call("query_order", '{"order_no": "A"}')),
    text_response("订单 A 已发货", usage=Usage(total_tokens=42)),
]


@pytest.mark.parametrize("saver", ALL_SAVERS, indirect=True)
async def test_run_state_recovers_identically(saver: CheckpointSaver, pg_thread_id):
    """同一段脚本跑完, 各实现恢复出来的进度与观察值完全一样."""
    counting_tool, _ = make_counting_tool()
    loop = AgentLoop(
        model=ScriptedModel(SAME_RUN_SCRIPT),
        tools=[counting_tool],
        saver=saver,
        thread_id=pg_thread_id,
    )

    result = await loop.run([{"role": "user", "content": "订单 A 到哪了"}])

    frame = await saver.load_latest(pg_thread_id)
    assert frame is not None
    assert frame.state.messages == result.messages
    assert frame.state.turn_count == result.turn_count == 2
    assert frame.state.total_tokens == result.total_tokens == 42
    # 观察值也一致: 最后一帧是正常跑完的, 且记了本轮用量
    assert frame.metadata.outcome == "finished"
    assert frame.metadata.turn_tokens == 42


@pytest.mark.parametrize("saver", ALL_SAVERS, indirect=True)
async def test_resume_parity(saver: CheckpointSaver, pg_thread_id):
    """断点续跑在各实现上结果一致: 工具只跑一次, 答案来自第二段."""
    counting_tool, calls = make_counting_tool()
    interrupted = AgentLoop(
        model=ScriptedModel(
            [tool_call_response(make_tool_call("query_order", '{"order_no": "A"}'))]
        ),
        tools=[counting_tool],
        saver=saver,
        thread_id=pg_thread_id,
        guard=LoopGuard(max_turns=1),
    )
    await interrupted.run([{"role": "user", "content": "订单 A 到哪了"}])

    checkpoint = await saver.load_latest(pg_thread_id)
    assert checkpoint is not None
    resumed_loop = AgentLoop(
        model=ScriptedModel([text_response("订单 A 已发货")]),
        tools=[counting_tool],
        saver=saver,
        thread_id=pg_thread_id,
    )

    result = await resumed_loop.resume(checkpoint)

    assert result.content == "订单 A 已发货"
    assert result.turn_count == 2
    assert calls == ["A"]


@pytest.mark.parametrize("saver", ALL_SAVERS, indirect=True)
async def test_history_capability_matches_declaration(
    saver: CheckpointSaver, pg_thread_id
):
    """能力声明与实际行为对得上: 说没有历史的, 翻历史就明确报错 (不给空结果).

    这一条把「声明即契约」的教学点落成断言: 四组参数里只有 redis-latest 走 else 分支,
    另外三个都必须真的翻得出历史.
    """
    for index in (1, 2):
        await saver.save(
            make_checkpoint(
                thread_id=pg_thread_id,
                checkpoint_id=f"{pg_thread_id}-{index}",
                turn_number=index,
            )
        )

    if saver.capabilities.history:
        history = await saver.list_history(pg_thread_id)
        assert {frame.turn_number for frame in history} == {1, 2}
        # 能翻历史 = 能按编号回到某一帧 (time-travel 的入口)
        restored = await saver.load(f"{pg_thread_id}-1", thread_id=pg_thread_id)
        assert restored is not None
        assert restored.turn_number == 1
    else:
        with pytest.raises(CheckpointCapabilityError):
            await saver.list_history(pg_thread_id)
        with pytest.raises(CheckpointCapabilityError):
            await saver.load(f"{pg_thread_id}-1", thread_id=pg_thread_id)

    # 四组参数都支持「取最新一帧」—— 这正是它们能被同一个 loop 用起来的前提
    latest = await saver.load_latest(pg_thread_id)
    assert latest is not None
    assert latest.turn_number == 2


@pytest.mark.parametrize("saver", HISTORY_SAVERS, indirect=True)
async def test_branching_stays_in_history_for_history_capable_savers(
    saver: CheckpointSaver, pg_thread_id
):
    """有历史的实现: 从老帧分出去的新线留在历史里, 老线也还在 (time-travel)."""
    origin = make_checkpoint(
        thread_id=pg_thread_id,
        checkpoint_id=f"{pg_thread_id}-origin",
        turn_number=1,
    )
    old_line = make_checkpoint(
        thread_id=pg_thread_id,
        checkpoint_id=f"{pg_thread_id}-old",
        turn_number=2,
        parent_id=origin.checkpoint_id,
    )
    new_line = make_checkpoint(
        thread_id=pg_thread_id,
        checkpoint_id=f"{pg_thread_id}-branch",
        turn_number=2,
        parent_id=origin.checkpoint_id,
    )
    for frame in (origin, old_line, new_line):
        await saver.save(frame)

    history = await saver.list_history(pg_thread_id)

    by_id = {frame.checkpoint_id: frame for frame in history}
    assert len(history) == 3
    assert by_id[new_line.checkpoint_id].parent_id == origin.checkpoint_id
    assert by_id[old_line.checkpoint_id].parent_id == origin.checkpoint_id


def test_capability_matrix_matches_the_contract():
    """能力矩阵被钉住 (造 saver 不必连库, 所以能静态断言)."""
    assert InMemoryCheckpointSaver().capabilities == CheckpointCapabilities(
        history=True, ttl=False
    )
    assert RedisCheckpointSaver(client=FakeRedisClient()).capabilities == (
        CheckpointCapabilities(history=True, ttl=False)
    )
    assert RedisCheckpointSaver(
        client=FakeRedisClient(), mode=MODE_LATEST, ttl_seconds=60
    ).capabilities == CheckpointCapabilities(history=False, ttl=True)
    assert PostgresCheckpointSaver(
        dsn="postgresql://user:pwd@127.0.0.1:5432/db"
    ).capabilities == CheckpointCapabilities(history=True, ttl=False)
