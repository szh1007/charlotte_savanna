"""HITL 挂起-恢复的 loop 面 (issue 34): 拦截 → 挂起 → 存档 → 恢复.

被测的是**运行停在半路**这条线, 三件事各守一段:

| 场景 | 断言 |
|---|---|
| 裁决点回了「需人工确认」 | 那条调用**不执行**、历史里欠着结果、 |
| | 终局事件是 approval_required |
| 挂起帧 | 来源是 `approval`、`state.suspension` 记着欠谁、事实里那条是 needs_approval |
| 同一批里两条要批 | 只挂第一条, 第二条按普通工具失败回填 |
| 恢复 (批准 / 拒绝) | 批准才执行; 拒绝把原因当工具结果回填, |
| | 模型据此继续答 |
| 恢复时的护栏 | 「需人工确认」被人的结论抵消, 但**拒绝类护栏照常生效** |
| 没有结论 | 当场报错, 那条调用一次都不跑 (绝不自动执行) |

上游那一半 (插件怎么写、`Decision` 三种态怎么校验) 归 `test_hooks.py`; 落库那一半
(工具调用行、`runs` 行) 归 `test_db_recorder.py` 与 `test_db_store.py`; 服务端那一半
(HTTP 路由、闸门、幂等键) 归 `test_server_approval.py` 与 `test_server_approval_db.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from helpers import make_checkpoint, make_metadata, make_state
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.agent import AgentLoop, Approval, LoopOutcome
from CharAgent.agent.utils.errors import LoopConfigError
from CharAgent.agent.utils.messages import APPROVAL_ALREADY_PENDING_TEXT
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.checkpoint.utils.types import (
    SUSPENSION_REASON_APPROVAL,
    CheckpointSource,
)
from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.stream.utils.types import EventType
from CharAgent.tool import Tool, tool

USER_MSG: dict[str, Any] = {"role": "user", "content": "帮我付了这一单"}
PAY_TEXT = "这一单要付款了, 需要你输一次支付密码"


@tool(annotations={"high_risk": True})
def pay_order(order_no: str) -> str:
    """支付一笔订单.

    Args:
        order_no: 订单号.
    """
    return f"订单 {order_no} 支付成功"


@tool
def query_order(order_no: str) -> str:
    """查询订单状态.

    Args:
        order_no: 订单号.
    """
    return "已发货"


class Gate:
    """核查插件 + 调用记录: 「需要人工确认」这条业务规则的最小形态.

    它按工具自己的注解表态 (框架只透传注解, 判断归业务) —— 于是
    `pay_order` 要人批, `query_order` 自己就过去了.

    attributes:
        calls: 被裁决过的工具名 (顺序即模型给的顺序) —— 「谁问过、谁没问过」的证据.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, **kwargs: Any) -> Decision | None:
        """裁决点上的插件 (要人工确认的那条返回 requires_approval)."""
        target: Tool = kwargs["tool"]
        self.calls.append(target.name)
        if target.annotations.get("high_risk"):
            return Decision.requires_approval(PAY_TEXT, needs=("payment_password",))
        return None


def make_gated_loop(
    model: MockLLM,
    saver: InMemoryCheckpointSaver | None = None,
    *,
    gate: Gate | None = None,
    event_sink: Any = None,
) -> tuple[AgentLoop, Gate]:
    """装一个挂了核查插件的 loop (要存档就一起给 saver 与 thread_id)."""
    hook = gate if gate is not None else Gate()
    hooks = HookRegistry()
    hooks.register(HookPoint.BEFORE_TOOL_EXECUTE, hook)
    loop = AgentLoop(
        model,
        [pay_order, query_order],
        hooks=hooks,
        event_sink=event_sink,
        saver=saver,
        thread_id="t-hitl" if saver is not None else None,
    )
    return loop, hook


class Collector:
    """事件收集 sink (按顺序收下, 断言类型与载荷用)."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def __call__(self, event: Any) -> None:
        self.events.append(event)

    @property
    def types(self) -> list[EventType]:
        return [event.type for event in self.events]

    @property
    def approval(self) -> Any:
        """那一条 approval_required 事件. 没有就报错 (用例自己写错了)."""
        return next(e for e in self.events if e.type is EventType.APPROVAL_REQUIRED)


# ---------------------------------------------------------------------------
# 拦截 → 挂起
# ---------------------------------------------------------------------------


async def test_a_high_risk_call_suspends_the_run_instead_of_executing():
    """裁决点回了「需人工确认」: 那条调用不执行, 整次运行停在半路等人."""
    model = MockLLM.scripted(
        [tool_call_response(make_tool_call("pay_order", '{"order_no": "A1"}'))]
    )
    loop, gate = make_gated_loop(model)

    result = await loop.run([USER_MSG])

    assert gate.calls == ["pay_order"]
    assert result.outcome is LoopOutcome.SUSPENDED, "停在半路, 不是跑完了"
    assert result.approval is not None
    assert result.approval.call.name == "pay_order"
    assert result.approval.prompt == PAY_TEXT
    assert result.approval.needs == ("payment_password",)
    # 欠着结果: 那条 assistant 消息在历史里, 但后面**没有**对应的 tool 消息 ——
    # 这正是 pending_tool_calls 认的挂起形状
    assert [m["role"] for m in result.messages] == ["user", "assistant"]
    assert result.messages[-1]["tool_calls"][0]["id"] == result.approval.call.id
    assert len(model.calls) == 1, "挂起后没有再问模型一次 (这一轮早就决策过了)"


async def test_the_suspension_ends_the_stream_with_approval_required():
    """终局事件换成 approval_required (带重建确认卡要的四个字段), 不是 final."""
    model = MockLLM.scripted(
        [tool_call_response(make_tool_call("pay_order", '{"order_no": "A1"}'))]
    )
    collector = Collector()
    loop, _ = make_gated_loop(model, event_sink=collector)

    await loop.run([USER_MSG])

    assert collector.types == [EventType.TOOL_CALL, EventType.APPROVAL_REQUIRED]
    assert collector.approval.data == {
        "tool_call_id": "call_pay_order",
        "tool_name": "pay_order",
        "prompt": PAY_TEXT,
        "needs": ["payment_password"],
        "turn": 1,
    }


async def test_the_suspension_frame_records_what_it_is_waiting_for():
    """挂起帧: 来源标 approval, 进度里记着欠哪一条调用 (恢复的起点)."""
    saver = InMemoryCheckpointSaver()
    model = MockLLM.scripted(
        [tool_call_response(make_tool_call("pay_order", '{"order_no": "A1"}'))]
    )
    loop, _ = make_gated_loop(model, saver)

    await loop.run([USER_MSG])

    frames = await saver.list_history("t-hitl")
    assert len(frames) == 1, "挂起也要落帧 (不然没有可恢复的起点)"
    frame = frames[0]
    assert frame.metadata.source is CheckpointSource.APPROVAL
    assert frame.state.suspension is not None
    assert frame.state.suspension.reason == SUSPENSION_REASON_APPROVAL
    assert [call.name for call in frame.state.suspension.pending] == ["pay_order"]
    assert frame.state.suspension.approval_id is None, "ADR-0014: 不加审批表"
    # 进度停在「问过模型一次」: 挂起不额外消耗轮数
    assert frame.state.turn_count == 1
    assert frame.metadata.outcome == LoopOutcome.SUSPENDED.value


async def test_only_the_first_call_of_a_batch_is_suspended():
    """同一批里两条都要审批: 只挂第一条, 第二条按普通工具失败回填.

    一次确认配一份一次性载荷 —— 两张卡同时弹出来, 用户输的那个密码给谁就成了
    说不清的事 (ADR-0014 的边界).
    """
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("pay_order", '{"order_no": "A1"}', call_id="call_0"),
                make_tool_call("pay_order", '{"order_no": "A2"}', call_id="call_1"),
            )
        ]
    )
    loop, _ = make_gated_loop(model)

    result = await loop.run([USER_MSG])

    assert result.approval is not None
    assert result.approval.call.id == "call_0", "挂的是模型先说的那一条"
    assert [m["role"] for m in result.messages] == ["user", "assistant", "tool"]
    backfilled = result.messages[-1]
    assert backfilled["tool_call_id"] == "call_1"
    assert backfilled["content"] == APPROVAL_ALREADY_PENDING_TEXT


async def test_a_normal_call_in_the_same_batch_still_runs():
    """同一批里不需要审批的那条照常执行、结果照常回填 (挂起不是整轮作废)."""
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("query_order", '{"order_no": "A1"}', call_id="call_0"),
                make_tool_call("pay_order", '{"order_no": "A1"}', call_id="call_1"),
            )
        ]
    )
    loop, gate = make_gated_loop(model)

    result = await loop.run([USER_MSG])

    assert gate.calls == ["query_order", "pay_order"]
    assert [m["role"] for m in result.messages] == ["user", "assistant", "tool"]
    assert result.messages[-1]["content"] == "已发货"  # 查询那条真的跑了
    assert result.approval is not None
    assert result.approval.call.id == "call_1"


async def test_a_plain_rejection_still_just_fails_the_call():
    """插件**拒绝**不是挂起: 工具不跑、原因回填, 运行照常走到答复."""
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("pay_order", '{"order_no": "A1"}')),
            text_response("那就不付了"),
        ]
    )
    hooks = HookRegistry()
    hooks.register(
        HookPoint.BEFORE_TOOL_EXECUTE, lambda **kw: Decision.reject("金额超过上限")
    )
    loop = AgentLoop(model, [pay_order], hooks=hooks)

    result = await loop.run([USER_MSG])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.approval is None
    assert result.messages[-2] == {
        "role": "tool",
        "tool_call_id": "call_pay_order",
        "content": "金额超过上限",
    }
    assert result.content == "那就不付了"


# ---------------------------------------------------------------------------
# 恢复: 批准 / 拒绝
# ---------------------------------------------------------------------------


async def suspend_once(saver: InMemoryCheckpointSaver) -> None:
    """先把一次运行跑到挂起 (恢复那条路的起点)."""
    loop, _ = make_gated_loop(
        MockLLM.scripted(
            [tool_call_response(make_tool_call("pay_order", '{"order_no": "A1"}'))]
        ),
        saver,
    )
    await loop.run([USER_MSG])


async def test_approving_completes_the_call_and_continues():
    """批准: 那条欠着的调用被执行, 结果回填, 模型接着把这一轮答完."""
    saver = InMemoryCheckpointSaver()
    await suspend_once(saver)
    frame = (await saver.list_history("t-hitl"))[-1]
    model = MockLLM.scripted([text_response("已经付好了")])
    loop, gate = make_gated_loop(model, saver)

    result = await loop.resume(frame, approval=Approval.approve())

    assert result.outcome is LoopOutcome.FINISHED
    assert result.approval is None
    assert result.content == "已经付好了"
    # 欠的那一条被执行了, 而前面那一轮**一步都没重跑** (模型只被问了这次的一回)
    assert [m["role"] for m in result.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert result.messages[2]["content"] == "订单 A1 支付成功"
    assert len(model.calls) == 1, "恢复不重跑已完成的轮次"
    # 轮数从挂起处接着数 (不从 1 重来). 补做那一轮**算一轮** (它同样落一帧), 所以
    # 恢复之后模型的下一次决策是第 3 回 —— 这是 ticket 22 起就有的口径
    assert result.turn_count == 3
    # 裁决点照常被叫到, 只是那一条已经有人批过了 (不会又挂一次)
    assert gate.calls == ["pay_order"]


async def test_the_resumed_call_keeps_guardrails_but_not_the_approval_rule():
    """恢复时「需人工确认」被人抵消, 但**拒绝类护栏照常生效**.

    挂起等待期里世界可能变了 (预算用完 / 额度关闭), 那些规矩不该因为人点过一次
    确认就静默放行.
    """
    saver = InMemoryCheckpointSaver()
    await suspend_once(saver)
    frame = (await saver.list_history("t-hitl"))[-1]
    hooks = HookRegistry()
    hooks.register(
        HookPoint.BEFORE_TOOL_EXECUTE, lambda **kw: Decision.reject("这笔已经超预算了")
    )
    # 同一个点上也挂着那条「要人工确认」的规则 (否则谈不上「被抵消」)
    hooks.register(HookPoint.BEFORE_TOOL_EXECUTE, Gate())
    loop = AgentLoop(
        MockLLM.scripted([text_response("那我换个办法")]),
        [pay_order],
        hooks=hooks,
        saver=saver,
        thread_id="t-hitl",
    )

    result = await loop.resume(frame, approval=Approval.approve())

    assert result.approval is None, "批过的调用不该再挂一次"
    assert result.messages[2]["content"] == "这笔已经超预算了"  # 护栏说了算
    assert result.content == "那我换个办法"


async def test_rejecting_does_not_execute_and_feeds_the_reason_back():
    """拒绝: 不执行, 原因当那条调用的结果回填, 模型据此继续答 (不是终止)."""
    saver = InMemoryCheckpointSaver()
    await suspend_once(saver)
    frame = (await saver.list_history("t-hitl"))[-1]
    model = MockLLM.scripted([text_response("好的, 那请你自己到订单页付")])
    loop, gate = make_gated_loop(model, saver)

    result = await loop.resume(frame, approval=Approval.reject("用户取消了这次付款"))

    assert result.outcome is LoopOutcome.FINISHED
    assert result.content == "好的, 那请你自己到订单页付"
    assert result.messages[2] == {
        "role": "tool",
        "tool_call_id": "call_pay_order",
        "content": "用户取消了这次付款",
    }
    assert gate.calls == [], "拒绝那一路根本不经过裁决点 (结论已经有人给了)"


async def test_resume_without_a_decision_is_refused_at_the_loop():
    """没有人的结论: 当场报错 (绝不自动执行) —— 命令行那条路的形状."""
    saver = InMemoryCheckpointSaver()
    await suspend_once(saver)
    frame = (await saver.list_history("t-hitl"))[-1]
    loop, _ = make_gated_loop(MockLLM.scripted([text_response("不该被问")]), saver)

    with pytest.raises(LoopConfigError, match="必须先有人给的结论"):
        await loop.resume(frame)


async def test_a_frame_without_a_suspension_resumes_without_an_approval():
    """普通快照 (没有挂起) 照旧恢复, 不需要人的结论 (行为与从前一字不变)."""
    saver = InMemoryCheckpointSaver()
    loop = AgentLoop(
        MockLLM.scripted([text_response("接着聊")]),
        [query_order],
        saver=saver,
        thread_id="t-plain",
    )
    checkpoint = make_checkpoint(
        thread_id="t-plain",
        checkpoint_id="ck-plain",
        state=make_state(
            messages=[{"role": "user", "content": "订单到哪了"}], turn_count=1
        ),
        metadata=make_metadata(),
    )
    await saver.save(checkpoint)

    result = await loop.resume(checkpoint)

    assert result.content == "接着聊"
    assert result.approval is None


async def test_the_suspension_does_not_leak_into_the_next_segment():
    """恢复段落的帧里**没有**挂起 (那是上一段的账), 来源也不再是 approval."""
    saver = InMemoryCheckpointSaver()
    await suspend_once(saver)
    frame = (await saver.list_history("t-hitl"))[-1]
    loop, _ = make_gated_loop(MockLLM.scripted([text_response("付好了")]), saver)

    await loop.resume(frame, approval=Approval.approve())

    frames = await saver.list_history("t-hitl")
    finished = frames[-1]
    assert finished.state.suspension is None
    assert finished.metadata.source is CheckpointSource.LOOP
    assert finished.metadata.outcome == LoopOutcome.FINISHED.value
