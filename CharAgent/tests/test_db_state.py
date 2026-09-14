"""状态机规则的自检 (不需要数据库): 该放行的放行, 该拦的拦住.

这批用例守的是**数据的自洽**: 一次运行的状态不能随便改. 最典型的坏数据是
「已经答完的 run 又变回运行中」—— 出现这种记录时, 任何按状态做的判断 (要不要
重跑、能不能取消、前端显示什么) 都会给出错的答案, 而且很难回溯是哪一步写坏的.

规则只有一张表 (`ALLOWED_TRANSITIONS`), 用例逐条对着它验: 表里写的都通得过、
表里没写的都拦得住、终态一个出口都没有.
"""

from __future__ import annotations

import pytest

from CharAgent.agent.utils.types import LoopOutcome
from CharAgent.db.entities import RunStatus
from CharAgent.db.errors import InvalidTransitionError
from CharAgent.db.state import (
    ALLOWED_TRANSITIONS,
    RUN_STATUS_FOR_OUTCOME,
    TERMINAL_RUN_STATUSES,
    can_transition,
    ensure_transition,
    run_status_for_outcome,
)

ALL_STATUSES = list(RunStatus)


def test_allow_table_covers_every_status():
    """每个状态都在表里有条目 —— 漏一个的状态会变成「哪儿都去不了」.

    这种漏很难发现: 它不会报错, 只会让某个状态变成事实上的死路, 而且未必测到.
    所以这里显式要求键集合等于全部取值.
    """
    assert set(ALLOWED_TRANSITIONS) == set(RunStatus)


def test_every_listed_transition_is_allowed():
    """允许表里每一条都真的放行 (表与判定函数不能各说各话)."""
    for from_status, targets in ALLOWED_TRANSITIONS.items():
        for to_status in targets:
            assert can_transition(from_status, to_status), (
                f"表里写了 {from_status} -> {to_status}, 判定却说不行"
            )


def test_every_unlisted_transition_is_rejected():
    """两个状态之间的每一对组合: 表里没写的必须 False.

    穷举 8x8 的全部组合 —— 比挑几个「典型非法路径」更彻底, 也省得将来加状态时
    忘了补用例.
    """
    for from_status in ALL_STATUSES:
        for to_status in ALL_STATUSES:
            allowed = to_status in ALLOWED_TRANSITIONS[from_status]
            assert can_transition(from_status, to_status) is allowed, (
                f"{from_status} -> {to_status} 的判定与允许表不一致"
            )


def test_terminal_statuses_have_no_way_out():
    """三个终态走不出去 (这是「不重跑已完成动作」的第一道闸).

    终态不可逆的理由: 「已经答完的 run 又变成运行中」意味着要重跑一遍真实动作
    (退款重做一次就是事故). 想重跑应当**新建一个 run** —— 那是新的一次执行.
    """
    for status in TERMINAL_RUN_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == frozenset(), f"{status} 竟然有出口"

    assert (
        frozenset({RunStatus.FINISHED, RunStatus.FAILED, RunStatus.CANCELLED})
        == TERMINAL_RUN_STATUSES
    )


def test_cancelled_reachable_from_any_non_terminal():
    """cancelled 是「从哪儿都能进」的那个终态 (#18 kill switch).

    用户点取消时不关心 agent 正在干嘛 (在想 / 在调工具 / 在等人批) —— 都得停得
    下来. 这条如果漏了某个状态, 现象是「某些时刻取消按钮点了没反应」.
    """
    for status in ALL_STATUSES:
        if status in TERMINAL_RUN_STATUSES:
            continue
        assert can_transition(status, RunStatus.CANCELLED), f"{status} 停不下来"


def test_illegal_transition_reports_both_ends():
    """非法迁移的报错要说清「从哪到哪」与「本来能去哪儿」.

    报错信息就是排查界面: 只说「非法迁移」的话, 看日志的人还得回来翻这张表.
    """
    with pytest.raises(InvalidTransitionError) as excinfo:
        ensure_transition(RunStatus.FINISHED, RunStatus.RUNNING)

    error = excinfo.value
    assert error.from_status == "finished"
    assert error.to_status == "running"
    assert "终态" in error.allowed

    message = str(error)
    assert "finished" in message and "running" in message


def test_legal_transition_passes_silently():
    """合法迁移不抛错 (它是「检查并放行」, 不是「只报错」)."""
    ensure_transition(RunStatus.CREATED, RunStatus.RUNNING)


def test_guard_stops_count_as_finished():
    """**跑完了但没给出答案**算 finished, 不算 failed.

    guard 刹车 (轮数 / token / 超时) 与截断放弃都是「按规则主动停下的」—— 它们的
    降级话术由上层决定 (模板回复 / 转人工), 数据层只如实记一个 finished. 记成
    failed 会让「失败率」这个指标把正常收尾也算进去, 从此不可用.
    """
    for outcome in (
        LoopOutcome.MAX_TURNS,
        LoopOutcome.TOKEN_BUDGET,
        LoopOutcome.TIME_LIMIT,
        LoopOutcome.TRUNCATION_LIMIT,
    ):
        assert run_status_for_outcome(outcome) is RunStatus.FINISHED, outcome


def test_server_interruption_counts_as_failed():
    """上游把生成打断了 (内容可能只有半截) 才算 failed.

    半截内容不能当答复给用户 —— 这与「主动停下」是两回事, 状态上也该分开.
    """
    assert run_status_for_outcome(LoopOutcome.SERVER_INTERRUPTED) is RunStatus.FAILED


def test_retrying_is_not_an_outcome():
    """`retrying` 不在「结束原因 → 状态」的映射里 (它是过程状态).

    一次运行最终停在 retrying 上是不可能的: 重试要么成功接着跑, 要么耗尽后以
    别的 outcome 收尾. 这条用例防的是「有人把 retrying 也塞进映射表」—— 那样
    收尾逻辑就会把一个中间态写成最终态.
    """
    assert RunStatus.RETRYING not in RUN_STATUS_FOR_OUTCOME.values()


def test_unknown_outcome_raises():
    """映射表里没有的结束原因抛 KeyError, 不猜一个状态写进库.

    猜错的代价是库里出现一个与事实不符的状态, 而且没人知道它是猜的.
    """

    class FakeOutcome:
        value = "not_a_real_outcome"

    with pytest.raises(KeyError):
        run_status_for_outcome(FakeOutcome())  # type: ignore[arg-type]
