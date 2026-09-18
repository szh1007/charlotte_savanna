"""checkpoint 内存实现测试 (内存版语义).

内存版是三实现里能力最全的一个 (能存、能取最新、能按编号取、能翻历史), 所以它
的用例其实是「协议该有的行为」的基准 —— 另两个实现少做的那部分, 在各自的用例里
表现为「明确报错」而不是「悄悄返回空结果」.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from helpers import make_checkpoint

from CharAgent.checkpoint.memory import InMemoryCheckpointSaver


async def test_save_then_load_latest_returns_frame():
    """存一帧后能取回同一帧 (内容完全一致)."""
    saver = InMemoryCheckpointSaver()
    checkpoint = make_checkpoint()

    await saver.save(checkpoint)

    assert await saver.load_latest("thread-1") == checkpoint


async def test_load_latest_returns_newest_frame():
    """存了三帧, 「最新」是最后存的那帧."""
    saver = InMemoryCheckpointSaver()
    frames = [
        make_checkpoint(
            checkpoint_id=f"ck-{index}",
            turn_number=index,
            created_at=datetime(2026, 9, 13, 10, index, tzinfo=UTC),
        )
        for index in (1, 2, 3)
    ]
    for frame in frames:
        await saver.save(frame)

    latest = await saver.load_latest("thread-1")

    assert latest is not None
    assert latest.checkpoint_id == "ck-3"


async def test_load_by_id_returns_matching_frame():
    """按编号取能拿到任意一帧 (time-travel 挑起点就靠它)."""
    saver = InMemoryCheckpointSaver()
    await saver.save(make_checkpoint(checkpoint_id="ck-1", turn_number=1))
    await saver.save(make_checkpoint(checkpoint_id="ck-2", turn_number=2))

    frame = await saver.load("ck-1")

    assert frame is not None
    assert frame.turn_number == 1


async def test_load_returns_none_for_unknown_id():
    """编号不存在: 返回 None (而不是抛错, 调用方自己判断)."""
    saver = InMemoryCheckpointSaver()

    assert await saver.load("没有这个编号") is None


async def test_load_latest_returns_none_for_unknown_thread():
    """会话不存在: 返回 None (「没存过」是正常情况, 不是错误)."""
    saver = InMemoryCheckpointSaver()

    assert await saver.load_latest("没存过") is None


async def test_list_history_returns_frames_in_save_order():
    """翻历史: 从早到晚 (存进去的顺序就是时间顺序)."""
    saver = InMemoryCheckpointSaver()
    for index in (1, 2, 3):
        await saver.save(
            make_checkpoint(
                checkpoint_id=f"ck-{index}",
                turn_number=index,
                created_at=datetime(2026, 9, 13, 10, index, tzinfo=UTC),
            )
        )

    history = await saver.list_history("thread-1")

    assert [frame.checkpoint_id for frame in history] == ["ck-1", "ck-2", "ck-3"]


async def test_list_history_limit_keeps_newest_in_oldest_first_order():
    """limit=2 取最近两帧, 返回顺序仍是从早到晚 (翻历史最常用的读法)."""
    saver = InMemoryCheckpointSaver()
    for index in (1, 2, 3):
        await saver.save(
            make_checkpoint(
                checkpoint_id=f"ck-{index}",
                turn_number=index,
                created_at=datetime(2026, 9, 13, 10, index, tzinfo=UTC),
            )
        )

    history = await saver.list_history("thread-1", limit=2)

    assert [frame.checkpoint_id for frame in history] == ["ck-2", "ck-3"]


async def test_list_history_limit_not_positive_returns_empty():
    """limit 不是正数: 「最近 0 帧」就是没有 (不报错, 也不给全部)."""
    saver = InMemoryCheckpointSaver()
    await saver.save(make_checkpoint())

    assert await saver.list_history("thread-1", limit=0) == []


async def test_list_history_for_unknown_thread_is_empty():
    """没存过的会话: 历史是空列表 (与「有历史但一帧没存」不冲突)."""
    saver = InMemoryCheckpointSaver()

    assert await saver.list_history("没存过") == []


async def test_saving_same_id_twice_keeps_the_first_frame():
    """同编号重复保存是空操作 (与 Postgres 的 ON CONFLICT DO NOTHING 对齐).

    为什么这么定: 存到一半失败、调用方重试一次时, 不能因为「第二次内容稍有不同」
    就悄悄改掉已经存下的那一帧 —— 那会让两处记录的进度对不上.
    """
    saver = InMemoryCheckpointSaver()
    await saver.save(make_checkpoint(checkpoint_id="ck-1", turn_number=1))
    await saver.save(make_checkpoint(checkpoint_id="ck-1", turn_number=99))

    frame = await saver.load("ck-1")

    assert frame is not None
    assert frame.turn_number == 1


async def test_saved_frame_is_detached_from_caller():
    """存进去之后, 调用方再改自己那份不影响存储 (深拷贝)."""
    saver = InMemoryCheckpointSaver()
    checkpoint = make_checkpoint()
    await saver.save(checkpoint)

    checkpoint.state.turn_count = 99

    stored = await saver.load_latest("thread-1")
    assert stored is not None
    assert stored.state.turn_count == 1


async def test_returned_frame_is_detached_from_store():
    """取出来的那份改着玩, 不影响存储里的内容 (下次取还是原样)."""
    saver = InMemoryCheckpointSaver()
    await saver.save(make_checkpoint())

    first = await saver.load_latest("thread-1")
    assert first is not None
    first.state.messages.clear()

    second = await saver.load_latest("thread-1")
    assert second is not None
    assert second.state.messages


async def test_branch_parent_is_preserved():
    """分支关系原样保存: 新帧的 parent_id 指向老帧 (time-travel 的骨架)."""
    saver = InMemoryCheckpointSaver()
    await saver.save(make_checkpoint(checkpoint_id="ck-1", turn_number=1))
    await saver.save(
        make_checkpoint(
            checkpoint_id="ck-2", turn_number=1, parent_id="ck-1", run_id="run-2"
        )
    )

    branch = await saver.load("ck-2")

    assert branch is not None
    assert branch.parent_id == "ck-1"


async def test_history_order_follows_created_at_not_insertion_order():
    """历史顺序看的是记录里的**时刻**, 不是「谁先被存进来」.

    为什么特意钉住: 内存版与 Postgres 要能逐条对比 (双实现对比用例),
    而 Postgres 是按 created_at 排序的 —— 若内存版按插入顺序排, 两者在「乱序
    存入」时就会给出不同结果.
    """
    saver = InMemoryCheckpointSaver()
    earlier = make_checkpoint(checkpoint_id="ck-1", created_at=datetime(2026, 9, 13))
    later = make_checkpoint(
        checkpoint_id="ck-2",
        created_at=datetime(2026, 9, 13) + timedelta(minutes=1),
    )

    await saver.save(later)
    await saver.save(earlier)

    history = await saver.list_history("thread-1")
    assert [frame.checkpoint_id for frame in history] == ["ck-1", "ck-2"]
    latest = await saver.load_latest("thread-1")
    assert latest is not None
    assert latest.checkpoint_id == "ck-2"


async def test_capabilities_declare_history_without_ttl():
    """能力声明: 有历史、不会过期 (调用方据此决定能不能 time-travel)."""
    saver = InMemoryCheckpointSaver()

    assert saver.capabilities.history is True
    assert saver.capabilities.ttl is False


async def test_aclose_keeps_saver_usable():
    """没有连接可关: 关完还能接着用 (形状与另两个实现一致, 但不是真的关)."""
    saver = InMemoryCheckpointSaver()
    await saver.save(make_checkpoint())

    await saver.aclose()

    assert await saver.load_latest("thread-1") is not None
