"""实体与枚举的自检 (不需要数据库): 取值与设计文档逐条对齐.

关注点是**契约**: 状态机有哪几个取值、哪些算终态、agent loop 的结束原因怎么翻成
状态. 这些一旦写错, 库里的值就跟文档对不上, 而数据库不会拦 (列是普通字符串).

为什么值得逐条钉住: 状态名是**跨版本契约** —— 前端按状态渲染、运维按状态排查、
将来加的新状态会跟老数据混在一张表里. 改一个取值要同步 docs/CONTEXT.md 与
docs/design/02-data-model.md §1, 这些用例就是提醒你「还有那两处要改」的那道闸.
"""

from __future__ import annotations

from CharAgent.agent.utils.types import LoopOutcome
from CharAgent.db.entities import (
    MessageRole,
    RunStatus,
    ThreadStatus,
    ToolCallStatus,
)


def test_run_status_has_the_eight_documented_values():
    """运行状态机八态 —— 与 issue 08 的原文与 docs/CONTEXT.md 一致.

    `retrying` 是容易被漏掉的那一个: 它描述的是「上游失败正在退避重试」这个
    **过程**状态 (P0-5 的重试发生在模型调用层, 同样要反映到运行状态上).
    """
    assert [status.value for status in RunStatus] == [
        "created",
        "running",
        "waiting_tool",
        "waiting_user",
        "retrying",
        "failed",
        "finished",
        "cancelled",
    ]


def test_thread_status_values():
    """会话状态三态 (02-data-model.md §1 Thread)."""
    assert [status.value for status in ThreadStatus] == [
        "active",
        "closed",
        "escalated",
    ]


def test_message_role_values_match_wire_roles():
    """消息角色与 wire 消息的 role 取值**逐一相同** —— 于是两个世界不用翻译.

    这条对应关系是分层的基石: 判断「这条能不能给前端看」时, 看的就是这个 role
    (见 conversation.py). 一旦两边取值分叉, 分层规则就会判错.
    """
    assert [role.value for role in MessageRole] == [
        "user",
        "assistant",
        "tool",
        "system",
    ]


def test_tool_call_status_includes_needs_approval():
    """工具调用状态含 `needs_approval` (HITL 挂起 #25).

    漏了它, 「等高危操作审批」那一步就无处可记 —— 只能借用 `running`, 于是
    「在跑」与「在等人」再也分不开, 前端也没法提示「去审批台看一眼」.
    """
    assert [status.value for status in ToolCallStatus] == [
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "needs_approval",
    ]


def test_every_loop_outcome_maps_to_a_run_status():
    """agent loop 的每个结束原因都有对应的运行状态 (一个都不能漏).

    漏了的话 `run_status_for_outcome` 会抛 KeyError —— 与其在收尾时炸, 不如在
    这里就发现「LoopOutcome 加了新成员而映射表没跟上」.
    """
    from CharAgent.db.state import RUN_STATUS_FOR_OUTCOME

    missing = [
        outcome for outcome in LoopOutcome if outcome not in RUN_STATUS_FOR_OUTCOME
    ]
    assert not missing, f"这些结束原因没有映射: {missing}"
