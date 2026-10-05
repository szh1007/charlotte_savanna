"""实体与枚举的自检 (不需要数据库): 取值与设计约定逐条对齐.

关注点是**契约**: 状态机有哪几个取值、哪些算终态、agent loop 的结束原因怎么翻成
状态. 这些一旦写错, 库里的值就跟契约对不上, 而数据库不会拦 (列是普通字符串).

为什么值得逐条钉住: 状态名是**跨版本契约** —— 前端按状态渲染、运维按状态排查、
将来加的新状态会跟老数据混在一张表里. 改一个取值要同步实体定义与所有下游引用,
这些用例就是提醒你「还有几处要改」的那道闸.
"""

from __future__ import annotations

from CharAgent.agent.utils.types import LoopOutcome
from CharAgent.db.entities import (
    KIND_BEHAVIORS,
    MemoryKind,
    MessageRole,
    RunStatus,
    ThreadStatus,
    ToolCallStatus,
)


def test_run_status_has_the_eight_documented_values():
    """运行状态机八态 —— 与数据层状态机定义一致.

    `retrying` 是容易被漏掉的那一个: 它描述的是「上游失败正在退避重试」这个
    **过程**状态 (重试发生在模型调用层, 同样要反映到运行状态上).
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
    """会话状态三态 (Thread 实体定义)."""
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


def test_memory_kind_values():
    """记忆四种 (C30): 累积型的两个 + 替换型的两个.

    #31 列了四层, 这里落的是其中两层 (情景 / 语义) 加两个「对助手的偏好」
    (风格 / 称呼) —— 短期记忆是对话上下文 (由会话与快照管着, 不在记忆表里),
    程序性记忆 (行为模式) 是另一个量级的事, 不假装四层都做了.
    """
    assert [kind.value for kind in MemoryKind] == [
        "episodic",
        "semantic",
        "style",
        "nickname",
    ]


def test_every_kind_has_a_behavior_defined():
    """每条 kind 都在行为表里, 标签齐全且不重复 —— 漏一个就是运行时 KeyError.

    与 `test_every_loop_outcome_maps_to_a_run_status` 同款做法 (C30): 新增一个
    kind 却忘了想清它的行为 (累积还是替换), 是这张表最该拦住的事.
    """
    assert set(KIND_BEHAVIORS) == set(MemoryKind)

    labels = [behavior.label for behavior in KIND_BEHAVIORS.values()]
    assert all(labels), "每条 kind 都要有给模型看的标签"
    assert len(labels) == len(set(labels)), "标签重复会让 recall 分不清种类"


def test_the_replace_kinds_are_the_expected_ones():
    """替换型就是风格与称呼这两条 —— 增删是行为变更, 要在这里留下痕迹.

    (「同一时刻只留一条」这件事只该发生在真的只有一个『槽』的概念上; 把一条
    事实类错标成替换型, 会让它悄悄顶掉别的记忆 —— 与 C12 的累积语义相反.)
    """
    replacing = {
        kind for kind, behavior in KIND_BEHAVIORS.items() if behavior.replaces_previous
    }
    assert replacing == {MemoryKind.STYLE, MemoryKind.NICKNAME}


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
