"""断点续跑 / time-travel / 挂起点恢复的集成测试 (核心验收面).

被测的是 agent loop 与 checkpoint 的接线 (agent/loop.py), 用内存版 saver (最省事
的存储) + 脚本化模型 (不碰真 API). 五条主线:

1. **每 Turn 落盘**: 一帧一帧存下去, 后一帧指向前一帧 (parent_id 串成链)
2. **观察值**: 每帧记下来源 (loop / fork / suspension) 与本轮用量 —— 回放调试的底子
3. **不重复已完成动作**: 从快照接着跑, 已经跑过的工具不会再跑一遍 (#5 的验收点)
4. **time-travel**: 从老快照恢复 → 新帧挂在老帧下面, 历史岔出一条新分支
5. **挂起点恢复 (#25 打底)**: 快照停在「工具还没有结果」的半路时, 先补做欠的
   调用再继续 —— 不重复问模型一次, 也不重跑已完成的轮次

外加三条边界: 配置写错 (saver 与 thread_id 不成对 / 拿错会话的快照), 落盘失败
(向上抛, 不吞), 以及「普通 run 不替调用方补做欠账」这条行为边界.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from helpers import make_checkpoint, make_metadata, make_state
from mock_llm import ScriptedModel, make_tool_call, text_response, tool_call_response

from CharAgent.agent.guard import LoopGuard
from CharAgent.agent.loop import AgentLoop
from CharAgent.agent.utils.errors import LoopConfigError
from CharAgent.agent.utils.messages import assistant_wire
from CharAgent.agent.utils.types import LoopOutcome
from CharAgent.checkpoint.memory import InMemoryCheckpointSaver
from CharAgent.checkpoint.utils.errors import (
    CheckpointConfigError,
    CheckpointStorageError,
)
from CharAgent.checkpoint.utils.types import (
    Checkpoint,
    CheckpointSource,
    Suspension,
)
from CharAgent.model.utils.types import Usage
from CharAgent.stream.utils.types import EventType
from CharAgent.tool import Tool, tool


@dataclass(slots=True)
class CountingTool:
    """一个会记账的工具 + 它的调用记录 (断点续跑的核心证据).

    「没重复执行」这件事唯一拿得出的证据就是调用次数, 所以工具必须自己数着.
    """

    tool: Tool
    calls: list[str]


def make_counting_tool(result: str = "订单已发货") -> CountingTool:
    """造一个「数自己被调了几次」的工具 (参数 order_no 记进 calls)."""
    calls: list[str] = []

    @tool(name="query_order", description="按订单号查询订单状态")
    def query_order(order_no: str) -> str:
        calls.append(order_no)
        return result

    return CountingTool(tool=query_order, calls=calls)


def make_loop(model: ScriptedModel, tools: list[Tool], **overrides) -> AgentLoop:
    """造一个接了内存版 saver 的 loop (默认会话 t-1; 用例按需覆盖字段)."""
    fields: dict = {
        "model": model,
        "tools": tools,
        "saver": InMemoryCheckpointSaver(),
        "thread_id": "t-1",
    }
    fields.update(overrides)
    return AgentLoop(**fields)


async def frames_of(saver: InMemoryCheckpointSaver, thread_id: str) -> list[Checkpoint]:
    """取某会话的全部快照 (按时间从早到晚)."""
    return await saver.list_history(thread_id)


def frame_for_turn(frames: list[Checkpoint], turn_number: int) -> Checkpoint:
    """按轮次取那一帧.

    刻意不靠列表位置: 同一毫秒存下的两帧, 顺序由编号 (随机的 uuid) 决定, 位置
    不可靠 —— 这类断言在 Windows 的时钟精度下会偶发地飘.
    """
    matched = [frame for frame in frames if frame.turn_number == turn_number]
    assert len(matched) == 1, (
        f"轮次 {turn_number} 的快照应恰好一帧, 实际 {len(matched)}"
    )
    return matched[0]


# ---------------------------------------------------------------------------
# 每 Turn 落盘
# ---------------------------------------------------------------------------


async def test_each_turn_saves_one_frame_chained_by_parent():
    """两轮的 run 落两帧, 后一帧的 parent_id 指向前一帧 (快照串成链)."""
    counting = make_counting_tool()
    loop = make_loop(
        ScriptedModel(
            [
                tool_call_response(make_tool_call("query_order", '{"order_no": "A"}')),
                text_response("订单 A 已发货"),
            ]
        ),
        [counting.tool],
    )

    await loop.run([{"role": "user", "content": "订单 A 到哪了"}])

    frames = await frames_of(loop._saver, "t-1")
    first = frame_for_turn(frames, 1)
    second = frame_for_turn(frames, 2)
    assert first.parent_id is None
    assert second.parent_id == first.checkpoint_id
    assert second.state.messages[-1] == {
        "role": "assistant",
        "content": "订单 A 已发货",
    }


async def test_frame_records_identity_and_observation_fields():
    """每帧记下「谁、哪次运行、跑到哪轮」以及观察值 (来源 / 用量 / 结束原因)."""
    loop = make_loop(
        ScriptedModel(
            [
                tool_call_response(make_tool_call("query_order", "{}")),
                text_response("订单已发货", usage=Usage(total_tokens=42)),
            ]
        ),
        [make_counting_tool().tool],
    )

    await loop.run([{"role": "user", "content": "订单到哪了"}], run_id="run-x")

    frames = await frames_of(loop._saver, "t-1")
    mid_run = frame_for_turn(frames, 1)
    finished = frame_for_turn(frames, 2)
    assert mid_run.thread_id == "t-1"
    assert mid_run.run_id == "run-x"
    # 进度里只有「接着跑要用的」: 结束原因这类观察值在 metadata 里
    assert not hasattr(mid_run.state, "outcome")
    # 跑一半的帧写「还没结束」, 跑完的帧才写结束原因
    assert mid_run.metadata.outcome is None
    assert finished.metadata.outcome == LoopOutcome.FINISHED.value
    assert finished.metadata.finish_reason == "stop"
    assert finished.metadata.content == "订单已发货"
    assert finished.state.total_tokens == 42
    assert finished.metadata.turn_tokens == 42


async def test_frames_record_origin_and_per_step_cost():
    """观察值能回答「这一步怎么来的、花了多少」—— 回放调试要看的就是这些."""
    counting = make_counting_tool()
    loop = make_loop(
        ScriptedModel(
            [
                tool_call_response(
                    make_tool_call("query_order", '{"order_no": "A"}'),
                    usage=Usage(total_tokens=100),
                ),
                text_response("已发货", usage=Usage(total_tokens=30)),
            ]
        ),
        [counting.tool],
    )

    await loop.run([{"role": "user", "content": "订单 A 到哪了"}])

    frames = await frames_of(loop._saver, "t-1")
    tool_turn = frame_for_turn(frames, 1)
    answer_turn = frame_for_turn(frames, 2)
    assert tool_turn.metadata.source is CheckpointSource.LOOP
    assert tool_turn.metadata.tool_names == ["query_order"]
    assert tool_turn.metadata.turn_tokens == 100
    assert answer_turn.metadata.tool_names == []
    assert answer_turn.metadata.turn_tokens == 30
    # 本轮耗时是增量 (「哪一步最慢」比得出来), 且非负
    assert tool_turn.metadata.turn_elapsed_ms >= 0
    assert answer_turn.metadata.turn_elapsed_ms >= 0


async def test_run_without_saver_writes_nothing():
    """没配 saver: 一个字节都不落 (与未接 checkpoint 时行为一致)."""
    loop = AgentLoop(model=ScriptedModel([text_response("在的")]), tools=None)

    result = await loop.run([{"role": "user", "content": "在吗"}])

    assert result.content == "在的"


# ---------------------------------------------------------------------------
# 断点续跑: 不重复已完成动作
# ---------------------------------------------------------------------------


async def test_resume_finishes_run_without_repeating_finished_tool():
    """暂停后接着跑: 已完成的那次工具调用不会重跑, 最终答复来自第二段 (#5)."""
    counting = make_counting_tool()
    saver = InMemoryCheckpointSaver()
    interrupted = make_loop(
        ScriptedModel(
            [tool_call_response(make_tool_call("query_order", '{"order_no": "A"}'))]
        ),
        [counting.tool],
        saver=saver,
        guard=LoopGuard(max_turns=1),
    )
    stopped = await interrupted.run([{"role": "user", "content": "订单 A 到哪了"}])
    assert stopped.outcome is LoopOutcome.MAX_TURNS
    assert counting.calls == ["A"]

    resumed_loop = make_loop(
        ScriptedModel([text_response("订单 A 已发货")]), [counting.tool], saver=saver
    )
    checkpoint = await saver.load_latest("t-1")
    assert checkpoint is not None

    result = await resumed_loop.resume(checkpoint)

    assert result.outcome is LoopOutcome.FINISHED
    assert result.content == "订单 A 已发货"
    assert counting.calls == ["A"]  # 关键断言: 工具只执行过那一次
    assert result.messages[-1] == {"role": "assistant", "content": "订单 A 已发货"}


async def test_resume_first_frame_is_marked_as_fork():
    """续跑落下的第一帧标 fork, 之后按普通轮记 (回放时一眼看出分叉点)."""
    counting = make_counting_tool()
    saver = InMemoryCheckpointSaver()
    interrupted = make_loop(
        ScriptedModel([tool_call_response(make_tool_call("query_order", "{}"))]),
        [counting.tool],
        saver=saver,
        guard=LoopGuard(max_turns=1),
    )
    await interrupted.run([{"role": "user", "content": "订单到哪了"}])
    checkpoint = await saver.load_latest("t-1")
    assert checkpoint is not None

    await make_loop(
        ScriptedModel(
            [
                tool_call_response(make_tool_call("query_order", "{}")),
                text_response("已发货"),
            ]
        ),
        [counting.tool],
        saver=saver,
    ).resume(checkpoint)

    frames = await frames_of(saver, "t-1")
    assert frame_for_turn(frames, 2).metadata.source is CheckpointSource.FORK
    assert frame_for_turn(frames, 3).metadata.source is CheckpointSource.LOOP


async def test_resume_continues_counters_from_checkpoint():
    """计数器跨断点接续: 累计 token 已超预算时, 续跑立刻刹车 (不再问模型)."""
    counting = make_counting_tool()
    saver = InMemoryCheckpointSaver()
    interrupted = make_loop(
        ScriptedModel(
            [
                tool_call_response(
                    make_tool_call("query_order", "{}"),
                    usage=Usage(total_tokens=500),
                )
            ]
        ),
        [counting.tool],
        saver=saver,
        guard=LoopGuard(max_turns=1),
    )
    await interrupted.run([{"role": "user", "content": "订单到哪了"}])

    model = ScriptedModel([text_response("这段不该被用到")])
    resumed_loop = make_loop(
        model, [counting.tool], saver=saver, guard=LoopGuard(max_total_tokens=100)
    )
    checkpoint = await saver.load_latest("t-1")
    assert checkpoint is not None

    result = await resumed_loop.resume(checkpoint)

    assert result.outcome is LoopOutcome.TOKEN_BUDGET
    assert model.calls == []  # 预算在续跑的第一时间就按累计值判定


async def test_resume_result_counts_are_cumulative_for_turns():
    """续跑结果的口径: turn_count 累计 (含续跑前), turns 只含本次 run 的轮次."""
    counting = make_counting_tool()
    saver = InMemoryCheckpointSaver()
    interrupted = make_loop(
        ScriptedModel([tool_call_response(make_tool_call("query_order", "{}"))]),
        [counting.tool],
        saver=saver,
        guard=LoopGuard(max_turns=1),
    )
    await interrupted.run([{"role": "user", "content": "订单到哪了"}])
    checkpoint = await saver.load_latest("t-1")
    assert checkpoint is not None

    result = await make_loop(
        ScriptedModel([text_response("已发货")]), [counting.tool], saver=saver
    ).resume(checkpoint)

    assert result.turn_count == 2  # 续跑前 1 轮 + 本次 1 轮
    assert len(result.turns) == 1  # 本次只跑了 1 轮
    assert result.turns[0].turn == 2  # 轮次编号接着数, 不从 1 重来


# ---------------------------------------------------------------------------
# time-travel: 从老快照恢复产生新分支
# ---------------------------------------------------------------------------


async def test_time_travel_from_older_checkpoint_creates_branch():
    """回到第一帧重跑: 新帧挂在老帧下面, 原来那条线原封不动 (历史分叉)."""
    counting = make_counting_tool()
    saver = InMemoryCheckpointSaver()
    original = make_loop(
        ScriptedModel(
            [
                tool_call_response(make_tool_call("query_order", '{"order_no": "A"}')),
                text_response("订单 A 已发货"),
            ]
        ),
        [counting.tool],
        saver=saver,
    )
    await original.run([{"role": "user", "content": "订单 A 到哪了"}])
    frames = await frames_of(saver, "t-1")
    origin = frame_for_turn(frames, 1)
    old_line = frame_for_turn(frames, 2)

    branched_loop = make_loop(
        ScriptedModel([text_response("换个说法: 订单 A 已发货")]),
        [counting.tool],
        saver=saver,
    )
    # 另起一次运行 (run_id 传给 resume): 分支不只是换个说法, 也是一次新执行
    branched = await branched_loop.resume(origin, run_id="run-branch")

    assert branched.content == "换个说法: 订单 A 已发货"
    assert counting.calls == ["A"]  # 回到第一帧后, 那次工具调用还在历史里, 不重跑
    known = {origin.checkpoint_id, old_line.checkpoint_id}
    frames = await frames_of(saver, "t-1")
    assert len(frames) == 3
    new_line = next(frame for frame in frames if frame.checkpoint_id not in known)
    assert new_line.parent_id == origin.checkpoint_id  # 挂在老帧下面 = 新分支
    assert new_line.run_id == "run-branch"
    assert new_line.metadata.source is CheckpointSource.FORK
    assert old_line.parent_id == origin.checkpoint_id  # 老分支还在, 没被覆盖


async def test_load_older_frame_by_id():
    """按编号取老快照 (time-travel 的入口) 在支持历史的实现上可用."""
    saver = InMemoryCheckpointSaver()
    await saver.save(make_checkpoint(checkpoint_id="ck-old", turn_number=1))
    await saver.save(make_checkpoint(checkpoint_id="ck-new", turn_number=2))

    restored = await saver.load("ck-old")

    assert restored is not None
    assert restored.turn_number == 1


# ---------------------------------------------------------------------------
# 挂起点恢复 (#25 打底): 补做欠下的工具调用, 不重跑
# ---------------------------------------------------------------------------


async def save_suspended_checkpoint(
    saver: InMemoryCheckpointSaver, *, tool_name: str = "query_order"
) -> Checkpoint:
    """造一帧「挂起点」快照: 历史末尾是已请求但没有结果的工具调用.

    这正是审批挂起的形状 —— 模型已经决定要调这个工具, 框架还没执行完 (或执行
    前等批准), 历史停在「欠一份结果」的地方.
    """
    pending = make_tool_call(tool_name, '{"order_no": "B"}', call_id="call_0")
    checkpoint = make_checkpoint(
        thread_id="t-1",
        checkpoint_id="ck-suspended",
        turn_number=1,
        state=make_state(
            messages=[
                {"role": "user", "content": "订单 B 到哪了"},
                assistant_wire(tool_call_response(pending)),
            ],
            turn_count=1,
            suspension=Suspension(
                reason="needs_approval",
                pending=[pending],
                approval_id="appr-1",
            ),
        ),
        metadata=make_metadata(
            source=CheckpointSource.SUSPENSION,
            content=None,
            finish_reason=None,
            outcome=None,
        ),
    )
    await saver.save(checkpoint)
    return checkpoint


async def test_resume_completes_pending_tool_calls_before_asking_model():
    """挂起恢复: 先补做欠下的调用, 再继续问模型 (顺序不能反)."""
    counting = make_counting_tool(result="订单 B 已发货")
    saver = InMemoryCheckpointSaver()
    checkpoint = await save_suspended_checkpoint(saver)
    model = ScriptedModel([text_response("订单 B 已发货, 预计明天送达")])

    result = await make_loop(model, [counting.tool], saver=saver).resume(checkpoint)

    assert counting.calls == ["B"]  # 欠的那一条补做了 (恰好一次)
    assert result.content == "订单 B 已发货, 预计明天送达"
    # 模型第一次被问到时, 历史末尾已经是补回来的工具结果
    assert model.calls[0]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_0",
        "content": "订单 B 已发货",
    }


async def test_resume_emits_events_for_completed_pending_calls():
    """补做挂起的调用时也照常发事件 (前端看到的顺序与正常工具轮一致)."""
    counting = make_counting_tool(result="订单 B 已发货")
    saver = InMemoryCheckpointSaver()
    checkpoint = await save_suspended_checkpoint(saver)
    seen: list[EventType] = []

    await make_loop(
        ScriptedModel([text_response("已发货")]),
        [counting.tool],
        saver=saver,
        event_sink=lambda event: seen.append(event.type),
    ).resume(checkpoint)

    assert seen == [EventType.TOOL_CALL, EventType.TOOL_RESULT, EventType.FINAL]


async def test_resume_saves_a_frame_for_the_completed_pending_turn():
    """补做的那一轮同样落盘 (轮次接着数、来源标 suspension), 过程本身也能再被打断."""
    counting = make_counting_tool()
    saver = InMemoryCheckpointSaver()
    checkpoint = await save_suspended_checkpoint(saver)

    await make_loop(
        ScriptedModel([text_response("已发货")]), [counting.tool], saver=saver
    ).resume(checkpoint)

    frames = await frames_of(saver, "t-1")
    completed = frame_for_turn(frames, 2)  # 补做的那轮 = 第 2 轮
    assert completed.parent_id == checkpoint.checkpoint_id
    assert completed.metadata.source is CheckpointSource.SUSPENSION
    assert completed.metadata.tool_names == ["query_order"]
    assert completed.state.messages[-1]["role"] == "tool"


async def test_plain_run_does_not_complete_history_pending_calls():
    """普通 run 不替调用方补做历史里的欠账 (补做只在 resume 路径, 只加不改).

    为什么这么定: run(messages) 的契约是「我就按你给的历史跑」, 顺手替调用方
    执行工具会让人意外; 要接着跑的场景本来就该走 resume (它接快照, 语义明确).
    """
    counting = make_counting_tool()
    pending = make_tool_call("query_order", '{"order_no": "B"}', call_id="call_0")
    loop = AgentLoop(
        model=ScriptedModel([text_response("好的")]), tools=[counting.tool]
    )

    await loop.run(
        [
            {"role": "user", "content": "订单 B 到哪了"},
            assistant_wire(tool_call_response(pending)),
        ]
    )

    assert counting.calls == []


# ---------------------------------------------------------------------------
# 边界: 配置写错 / 拿错存档 / 落盘失败
# ---------------------------------------------------------------------------


def test_saver_without_thread_id_is_rejected():
    """只给 saver 不给 thread_id: 构造期报错 (存哪儿 + 属于哪段对话缺一不可)."""
    with pytest.raises(LoopConfigError) as excinfo:
        AgentLoop(model=ScriptedModel([]), saver=InMemoryCheckpointSaver())

    assert "thread_id" in str(excinfo.value)


def test_thread_id_without_saver_is_rejected():
    """只给 thread_id 不给 saver: 同样是配置错误 (没有存档的地方)."""
    with pytest.raises(LoopConfigError) as excinfo:
        AgentLoop(model=ScriptedModel([]), thread_id="t-1")

    assert "saver" in str(excinfo.value)


def test_invalid_thread_id_is_rejected_at_construction():
    """thread_id 不合法 (含空格): 装配期就报错 (它之后会变成存储里的键名)."""
    with pytest.raises(CheckpointConfigError) as excinfo:
        AgentLoop(
            model=ScriptedModel([]),
            saver=InMemoryCheckpointSaver(),
            thread_id="bad id",
        )

    assert "thread_id" in str(excinfo.value)


async def test_resume_requires_saver_configuration():
    """没配 saver 的 loop 调 resume: 报错 (没有地方读快照)."""
    loop = AgentLoop(model=ScriptedModel([]))

    with pytest.raises(LoopConfigError):
        await loop.resume(make_checkpoint())


async def test_resume_rejects_checkpoint_of_another_thread():
    """拿别的会话的快照来恢复: 报错 (会把两段对话搅在一起)."""
    loop = make_loop(ScriptedModel([]), [], saver=InMemoryCheckpointSaver())

    with pytest.raises(LoopConfigError) as excinfo:
        await loop.resume(make_checkpoint(thread_id="t-2"))

    assert "t-2" in str(excinfo.value)


async def test_checkpoint_save_failure_propagates():
    """落盘失败向上抛 (不吞): 只有让调用方看见, 「以为存上了」才不会变成事故."""

    class FailingSaver(InMemoryCheckpointSaver):
        async def save(self, checkpoint: Checkpoint) -> None:
            raise CheckpointStorageError("磁盘满了")

    loop = AgentLoop(
        model=ScriptedModel([text_response("在的")]),
        tools=None,
        saver=FailingSaver(),
        thread_id="t-1",
    )

    with pytest.raises(CheckpointStorageError):
        await loop.run([{"role": "user", "content": "在吗"}])


# ---------------------------------------------------------------------------
# 接着同一段会话往下写 (ticket 17): run(parent_id=) + LoopResult.last_checkpoint_id
# ---------------------------------------------------------------------------


async def test_a_new_run_can_hang_its_first_frame_on_a_given_parent():
    """`run(parent_id=...)`: 这一段的头一帧挂在指定那帧下面 (而不是当新根).

    为什么要这个参数: 一次 run 从零起一个 LoopState, 于是同一段对话的每一轮提问
    都写出一条**新根**, 旧链变孤儿 —— 内容不丢, 但那棵树看起来是散的. 会话那侧
    把上一段的 `last_checkpoint_id` 递回来, 链就接上了 (见 ChatSession.ask).
    """
    saver = InMemoryCheckpointSaver()
    loop = make_loop(
        ScriptedModel([text_response("第一答"), text_response("第二答")]),
        [],
        saver=saver,
        thread_id="t-chain",
    )
    first = await loop.run([{"role": "user", "content": "第一问"}])
    second = await loop.run(
        [{"role": "user", "content": "第二问"}],
        parent_id=first.last_checkpoint_id,
    )

    frames = await frames_of(saver, "t-chain")
    assert first.last_checkpoint_id == frames[0].checkpoint_id
    assert second.last_checkpoint_id == frames[1].checkpoint_id
    assert frames[0].parent_id is None, "第一段自己起根"
    assert frames[1].parent_id == frames[0].checkpoint_id, "第二段接着第一段长"


async def test_the_result_carries_the_last_frame_id():
    """`LoopResult.last_checkpoint_id` 就是这一段落下的最后一帧 (没配 saver 时是 None).

    会话靠它把下一轮挂上来, 所以它必须是**最后一帧**的编号而不是随便一帧.
    """
    saver = InMemoryCheckpointSaver()
    loop = make_loop(
        ScriptedModel(
            [
                tool_call_response(make_tool_call("query_order", "{}")),
                text_response("订单已发货"),
            ]
        ),
        [make_counting_tool().tool],
        saver=saver,
    )

    result = await loop.run([{"role": "user", "content": "订单到哪了"}])

    frames = await frames_of(saver, "t-1")
    assert result.last_checkpoint_id == frames[-1].checkpoint_id
    assert len(frames) == 2, "两轮 = 两帧, 取的是后一帧"


async def test_without_a_saver_the_result_carries_no_frame_id():
    """没配 saver: 这个字段是 None (没有帧可指) —— 调用方据此知道「接不上链」."""
    loop = AgentLoop(ScriptedModel([text_response("答")]))

    result = await loop.run([{"role": "user", "content": "问"}])

    assert result.last_checkpoint_id is None
