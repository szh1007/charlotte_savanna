"""帧 ↔ 运行的双向溯源 (ticket 22): 两个编号各归其位, 而且对得上.

Ticket 22 之前, `charagent_checkpoints.run_id` 与 `charagent_runs.run_id` 是两个
各自生成的 uuid —— 名字一样、意思两样, 谁也 join 不上谁 (真机查库时一眼可见).
本页断的就是接上之后那几件事:

1. **帧属于哪一行账**: 每一帧的 `run_id` = 这一轮的运行行编号 (跑之前就定下的那个)
2. **这一行账落了哪几帧**: 运行行的 `last_checkpoint_id` = 本段落下的最后一帧,
   顺 `parent_id` 往回走能取到本次运行落的每一帧 (次序也与落盘次序一致)
3. **没记账的那一轮**: 帧上的 `run_id` 是 None (没配记录层 / 开账没成), 运行行那边
   自然也没有对应的一行 —— 两边同时缺席, 而不是各缺一半

模型走替身, 快照用内存版, 记录表用假库 (`tests/doubles.py`), 离线可跑.
"""

from __future__ import annotations

from typing import Any

from mock_llm import MockLLM, text_response

from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client import ChatSession
from CharAgent.db.entities import RunStatus
from CharAgent.db.recorder import ConversationRecorder
from CharAgent.tests.doubles import FakeRecordDatabase

THREAD_ID = "toy:u-9f3a:linkage"


def session_for(
    model: Any,
    saver: InMemoryCheckpointSaver,
    *,
    database: FakeRecordDatabase | None = None,
) -> ChatSession:
    """一期会话: 内存快照 + (可选) 记到假库里的记录员."""
    recorder = (
        None
        if database is None
        else ConversationRecorder(database=database, tenant_id="toy", user_id="u-9f3a")
    )
    return ChatSession(
        model,
        saver=saver,
        tools=None,
        thread_id=THREAD_ID,
        recorder=recorder,
    )


async def _frames_of(saver: InMemoryCheckpointSaver) -> list[Any]:
    """这段会话落的全部帧 (从早到晚)."""
    return await saver.list_history(THREAD_ID)


async def test_every_frame_points_at_the_run_row_that_produced_it() -> None:
    """帧 → 运行行: 一问一答各一张运行行, 每一帧指的就是自己那一次.

    「这几条 (这次请求的账本) 是哪一次问答产生的」由此从帧这边也答得上.
    """
    saver = InMemoryCheckpointSaver()
    database = FakeRecordDatabase()
    session = session_for(
        MockLLM.fixed(text_response("答好了")), saver, database=database
    )

    await session.ask("第一问")
    await session.ask("第二问")

    frames = await _frames_of(saver)
    run_rows = database.rows_of("charagent_runs")
    assert len(run_rows) == 2, "一问一行账"
    assert len(frames) == 2, "模型直接作答: 一问一帧 (有工具往返的那几轮才多帧)"
    assert [frame.run_id for frame in frames] == [
        run_rows[0]["run_id"],
        run_rows[1]["run_id"],
    ], "每一帧指向自己那一次运行的账目行"
    assert all(row["status"] == RunStatus.FINISHED.value for row in run_rows)


async def test_the_run_row_points_back_at_the_frames_of_that_run() -> None:
    """运行行 → 帧: `last_checkpoint_id` 是**本段落的最后一帧**, 顺链回取得全部帧.

    这是另一半: 「这次运行当时看到了什么」从账目行出发也能走到 —— 而且走到的是
    **本次运行**落的那几帧, 不是整段会话的 (链会一直连到会话开头, 所以要按
    `loop_id` 分段).
    """
    saver = InMemoryCheckpointSaver()
    database = FakeRecordDatabase()
    session = session_for(
        MockLLM.fixed(text_response("答好了")), saver, database=database
    )

    await session.ask("第一问")
    await session.ask("第二问")

    frames = await _frames_of(saver)
    [_, second_run] = database.rows_of("charagent_runs")
    assert second_run["last_checkpoint_id"] == frames[-1].checkpoint_id

    # 顺 parent_id 往回走: 直到 loop_id 变了为止 —— 那就是本次运行的边界
    by_id = {frame.checkpoint_id: frame for frame in frames}
    walked = []
    cursor = second_run["last_checkpoint_id"]
    while cursor is not None and by_id[cursor].loop_id == frames[-1].loop_id:
        walked.append(cursor)
        cursor = by_id[cursor].parent_id
    assert walked == [frame.checkpoint_id for frame in frames[1:]], (
        "取到的是第二问落的两帧 (次序与落盘一致: 后落的在前)"
    )


async def test_a_run_without_an_account_leaves_the_frame_unlinked() -> None:
    """没配记录层: 帧上的 `run_id` 是 None, 那边也没有多出一行 —— 两边同时缺席.

    「没记账」与「记成了空」是两回事: 前者是 None (事实), 后者会骗人 (好像真跑过
    一次、只是什么账都没有). 这里断的正是「两边同一种形状」.
    """
    saver = InMemoryCheckpointSaver()
    session = session_for(MockLLM.fixed(text_response("答好了")), saver)

    await session.ask("没人记账的问")

    frames = await _frames_of(saver)
    assert frames and all(frame.run_id is None for frame in frames)
