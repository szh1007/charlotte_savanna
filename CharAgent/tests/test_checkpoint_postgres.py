"""checkpoint Postgres 实现测试 (issue 07 / ADR-0002 第三种语义).

打的是 `pg` 标记 (需本机 Postgres; 默认被 addopts 排除). 单跑:

    pytest -m pg

每个用例用自己的会话号 (pg_thread_id fixture 发的唯一 id), 用完把那个会话的行
删掉 —— 所以可以放心跑在本机开发库上, 不会碰到别人的数据.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from conftest import conninfo
from helpers import make_checkpoint, make_metadata, make_state

from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.checkpoint.utils.pending import pending_tool_calls
from CharAgent.checkpoint.utils.types import (
    SCHEMA_VERSION,
    CheckpointSource,
    Suspension,
)
from CharAgent.db.schema import CHECKPOINTS_TABLE_NAME as CHECKPOINTS_TABLE

pytestmark = pytest.mark.pg


def make_frame(
    thread_id: str,
    turn: int,
    *,
    minutes: int = 0,
    label: str | None = None,
    **overrides,
):
    """造一帧带时刻的快照 (同一会话里按 minutes 拉开先后, 顺序断言才稳).

    label 用来给「轮次相同但属于不同分支」的两帧起不同编号 —— time-travel 分出的
    新线与老线轮次一样, 只有编号和时刻不同 (编号撞了会被当成重复保存而跳过).
    """
    name = label or f"ck{turn}"
    fields = {
        "thread_id": thread_id,
        "checkpoint_id": f"{thread_id}-{name}",
        "run_id": f"{thread_id}-run",
        "turn_number": turn,
        "created_at": datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
        + timedelta(minutes=minutes),
    }
    fields.update(overrides)
    return make_checkpoint(**fields)


async def test_ensure_schema_is_idempotent(pg_saver: PostgresCheckpointSaver):
    """建表建索引重复执行安全 (本包首次写入会自动调一次, alembic 也会调)."""
    await pg_saver.ensure_schema()

    await pg_saver.ensure_schema()


async def test_save_then_load_latest(pg_saver, pg_thread_id):
    """存一帧再取回来: 内容完全一致 (经列 + JSON 列往返之后)."""
    frame = make_frame(pg_thread_id, 1)

    await pg_saver.save(frame)
    loaded = await pg_saver.load_latest(pg_thread_id)

    assert loaded == frame


async def test_history_is_ordered_by_created_at(pg_saver, pg_thread_id):
    """翻历史: 按时刻从早到晚 (即使存入顺序是乱的)."""
    third = make_frame(pg_thread_id, 3, minutes=2)
    first = make_frame(pg_thread_id, 1, minutes=0)
    second = make_frame(pg_thread_id, 2, minutes=1)
    for frame in (third, first, second):
        await pg_saver.save(frame)

    history = await pg_saver.list_history(pg_thread_id)

    assert [frame.turn_number for frame in history] == [1, 2, 3]


async def test_list_history_limit_keeps_newest_in_oldest_first_order(
    pg_saver, pg_thread_id
):
    """limit=2 取最近两帧, 返回顺序仍是从早到晚."""
    for index in (1, 2, 3):
        await pg_saver.save(make_frame(pg_thread_id, index, minutes=index))

    history = await pg_saver.list_history(pg_thread_id, limit=2)

    assert [frame.turn_number for frame in history] == [2, 3]


async def test_list_history_limit_not_positive_returns_empty(pg_saver, pg_thread_id):
    """limit 不是正数: 返回空列表 (不必往库里跑一趟)."""
    await pg_saver.save(make_frame(pg_thread_id, 1))

    assert await pg_saver.list_history(pg_thread_id, limit=0) == []


async def test_saving_same_id_twice_is_noop(pg_saver, pg_thread_id):
    """同编号重复保存: 什么都不改 (ON CONFLICT DO NOTHING, 与内存版对齐)."""
    frame = make_frame(pg_thread_id, 1)
    await pg_saver.save(frame)

    await pg_saver.save(make_frame(pg_thread_id, 99, checkpoint_id=frame.checkpoint_id))

    loaded = await pg_saver.load(frame.checkpoint_id)
    assert loaded is not None
    assert loaded.turn_number == 1


async def test_load_returns_none_for_unknown_id(pg_saver):
    """编号不存在: 返回 None."""
    assert await pg_saver.load("根本没有这个编号") is None


async def test_load_latest_returns_none_for_unknown_thread(pg_saver):
    """会话不存在: 返回 None."""
    assert await pg_saver.load_latest("根本没这个会话") is None


async def test_time_travel_branch_keeps_both_lines(pg_saver, pg_thread_id):
    """从老快照分出一条新线: 两条线都留在历史里, 谁接着谁看 parent_id (#5)."""
    origin = make_frame(pg_thread_id, 1)
    old_line = make_frame(pg_thread_id, 2, minutes=1, parent_id=origin.checkpoint_id)
    await pg_saver.save(origin)
    await pg_saver.save(old_line)

    new_line = make_frame(
        pg_thread_id,
        2,
        minutes=2,
        label="ck2-branch",
        parent_id=origin.checkpoint_id,
    )
    await pg_saver.save(new_line)

    history = await pg_saver.list_history(pg_thread_id)
    by_id = {frame.checkpoint_id: frame for frame in history}
    assert len(history) == 3
    assert by_id[new_line.checkpoint_id].parent_id == origin.checkpoint_id
    assert by_id[old_line.checkpoint_id].parent_id == origin.checkpoint_id


def read_raw_row(pg_dsn, checkpoint_id: str) -> tuple | None:
    """绕过 saver 直接查库那一行 (同步执行, 由调用方放进线程).

    为什么用同步 psycopg: 与实现同一条理由 —— 异步连接在 Windows 的默认事件
    循环上跑不起来 (见 checkpoint/postgres.py 的 docstring 取舍 1).
    """
    with psycopg.connect(conninfo(pg_dsn), autocommit=True) as conn:
        cursor = conn.execute(
            f"SELECT state, metadata, schema_version, turn_number"
            f" FROM {CHECKPOINTS_TABLE} WHERE checkpoint_id = %s",
            (checkpoint_id,),
        )
        return cursor.fetchone()


def delete_frame(pg_dsn, checkpoint_id: str) -> None:
    """删掉一帧 (同步执行, 由调用方放进线程) —— 只为触发外键的删除动作."""
    with psycopg.connect(conninfo(pg_dsn), autocommit=True) as conn:
        conn.execute(
            f"DELETE FROM {CHECKPOINTS_TABLE} WHERE checkpoint_id = %s",
            (checkpoint_id,),
        )


async def test_json_columns_hold_progress_and_observation(
    pg_dsn, pg_saver, pg_thread_id
):
    """落库的形状: 身份字段成列, 进度与观察值各进一个 JSON 列 (v3 起).

    直接查库确认 (而不是只信读回来的对象): 「列存身份、JSON 存进度与观察值」
    是 02-data-model.md §3 定的存储形态, 值得钉一次 —— 顺带保证两块没被写反.
    """
    frame = make_frame(pg_thread_id, 1)
    await pg_saver.save(frame)

    row = await asyncio.to_thread(read_raw_row, pg_dsn, frame.checkpoint_id)

    assert row is not None
    state, metadata, schema_version, turn_number = row
    assert isinstance(state, dict)  # JSONB 自动解析成 Python 字典
    assert isinstance(metadata, dict)
    assert state["messages"] == frame.state.messages
    assert "content" not in state  # 观察值不在进度里 (v3 起分开)
    assert metadata["source"] == frame.metadata.source.value
    assert metadata["turn_tokens"] == frame.metadata.turn_tokens
    assert schema_version == SCHEMA_VERSION
    assert turn_number == 1


async def test_datetime_inside_state_survives_roundtrip(pg_saver, pg_thread_id):
    """进度里的 datetime (行李牌) 在 PG 上也能原样读回."""
    moment = datetime(2026, 9, 13, 8, 30, tzinfo=UTC)
    frame = make_frame(
        pg_thread_id,
        1,
        state=make_state(
            messages=[{"role": "tool", "content": "已发货", "at": moment}]
        ),
    )

    await pg_saver.save(frame)
    loaded = await pg_saver.load_latest(pg_thread_id)

    assert loaded is not None
    assert loaded.state.messages[0]["at"] == moment


async def test_suspension_survives_roundtrip(pg_saver, pg_thread_id):
    """挂起点 (还欠哪几条调用) 落库再读回来一字不差 (#25 的底子)."""
    pending = pending_tool_calls(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_0",
                        "type": "function",
                        "function": {
                            "name": "refund_order",
                            "arguments": '{"order_no": "20260701123456"}',
                        },
                    }
                ],
            }
        ]
    )
    frame = make_frame(
        pg_thread_id,
        1,
        state=make_state(
            suspension=Suspension(
                reason="needs_approval", pending=pending, approval_id="appr-1"
            ),
        ),
        metadata=make_metadata(
            source=CheckpointSource.SUSPENSION,
            content=None,
            finish_reason=None,
            outcome=None,
        ),
    )

    await pg_saver.save(frame)
    loaded = await pg_saver.load_latest(pg_thread_id)

    assert loaded is not None
    assert loaded.state.suspension == frame.state.suspension
    assert pending_tool_calls(loaded.state.messages) is not None


async def test_deleting_a_frame_orphans_its_branches(pg_dsn, pg_saver, pg_thread_id):
    """删掉一帧: 挂在它下面的分支不被连带删掉, 只是变成没有起点的孤儿 (SET NULL).

    这是 db/schema.py 里那个外键动作选择的效果 (ON DELETE SET NULL, 不是 CASCADE):
    删数据是危险动作, 宁可留下看着奇怪的记录, 也不要替调用方把下游数据一起抹掉.
    """
    origin = make_frame(pg_thread_id, 1)
    branch = make_frame(
        pg_thread_id, 2, minutes=1, label="ck2-branch", parent_id=origin.checkpoint_id
    )
    await pg_saver.save(origin)
    await pg_saver.save(branch)

    # saver 协议里没有删除 (清理策略属 P2-12), 所以直接一条 SQL 来触发外键行为
    await asyncio.to_thread(delete_frame, pg_dsn, origin.checkpoint_id)

    remaining = await pg_saver.load(branch.checkpoint_id)

    assert remaining is not None
    assert remaining.parent_id is None


async def test_capabilities_declare_history_without_ttl(pg_saver):
    """能力声明: 有全历史、不会过期 (time-travel 的前提)."""
    assert pg_saver.capabilities.history is True
    assert pg_saver.capabilities.ttl is False


async def test_saver_reconnects_after_close(pg_saver, pg_thread_id):
    """关掉连接后再用: 懒重连, 不必重新造 saver."""
    await pg_saver.save(make_frame(pg_thread_id, 1))
    await pg_saver.aclose()

    loaded = await pg_saver.load_latest(pg_thread_id)

    assert loaded is not None
    assert loaded.turn_number == 1
