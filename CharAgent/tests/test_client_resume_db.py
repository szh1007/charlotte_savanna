"""续跑落在**真库**里的行 (标 `pg_db`, 默认排除).

这一份验的是「续跑那一段的账落在哪儿」—— 它们都要真去库里看一眼才知道对不对:

- 命令行 `--resume` (不传 `run_id`): `charagent_runs` **多一行** (新的一次运行),
  这一段落的帧与消息都指着那一行
- 传了 `run_id` (HITL 的第二段): **不新增**行, 帧与消息指回传进来的那一行, 而
  **这一段的结局**照常写上去 (跑完了就是终态; 又挂起一次就是 `waiting_user`)
- HITL 的挂起那一段 (issue 34): 那次运行落成 `waiting_user` 且 `finished_at` 还是
  空 —— 那一次运行还开着, 人给了结论才接着跑

> **ticket 33 在这里写的是「第二段一笔都不碰那一行」, issue 34 改判了.** 改的理由:
> 那一行要写的**不是**「这一次运行结没结」, 而是「这一段跑成什么样」—— 挂起那一段
> 自己也要写 (写的是 `waiting_user`), 否则「它现在在等人」这件事在库里没有落点.
> 「还没结束」由 `finished_at IS NULL` 表达, 不再靠「什么都不写」.

装配用的是**真会话 + 真记录员 + 真库 + 真快照存储** (只有模型是替身 `MockLLM`),
于是「会话交了什么」与「库里落下什么」被一起覆盖 —— 中间没有替身可以藏错. 帧那一侧
必须是真的: 运行行的 `last_checkpoint_id` 是指向帧表的外键, 拿内存快照顶替会当场
撞上外键 (那正是「帧与运行行互相指着」这条契约在库里的样子). 会话那一层的形状
(交了什么、`since` 是多少) 由 `test_client_session.py` 的离线用例盯着, 这一份只管
**真库里的行**.

隔离与 `test_db_store.py` 一致: `charagent_test` schema, 用完整个删掉.
"""

from __future__ import annotations

from typing import Any

import pytest
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response
from sqlalchemy import select

from CharAgent.agent import Approval, LoopOutcome
from CharAgent.checkpoint import PostgresCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.db import (
    ConversationRecorder,
    PgDatabase,
    RunsRepository,
    RunStatus,
    ToolCallStatus,
)
from CharAgent.db.cost import PriceTable
from CharAgent.db.schema import messages
from CharAgent.db.schema import runs as runs_table
from CharAgent.db.schema import tool_calls as tool_calls_table
from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.tool import tool

pytestmark = pytest.mark.pg_db

THREAD_ID = "resume-db:u-1:chat-1"
# 全天一价那张表 (不用日历): 未命中 2 / 命中 0.5 / 输出 8 (每百万 token)
FLAT_PRICES = PriceTable.from_json(
    '{"models": {"deepseek-flash": {"cache_miss": 2, "cache_hit": 0.5, "output": 8}}}'
)


def _recorder(db: PgDatabase) -> ConversationRecorder:
    """造一个绑在玩具属主上的记录员 (价目表给死, 免得看环境变量)."""
    return ConversationRecorder(
        database=db, tenant_id="t-resume", user_id="u-1", prices=FLAT_PRICES
    )


def _session(model, saver, recorder) -> ChatSession:
    """造一个会话 (与命令行那套装配同形: 模型 / 快照 / 记录员都从外面给)."""
    return ChatSession(
        model,
        saver=saver,
        tools=[],
        thread_id=THREAD_ID,
        model_name="deepseek-flash",
        recorder=recorder,
    )


async def _run_rows(db: PgDatabase) -> list[Any]:
    """本会话的运行行 (按创建顺序)."""
    async with db.connect() as session:
        return list(
            session.execute(
                select(
                    runs_table.c.run_id,
                    runs_table.c.status,
                    runs_table.c.turn_count,
                    runs_table.c.finished_at,
                )
                .where(runs_table.c.thread_id == THREAD_ID)
                .order_by(runs_table.c.created_at)
            ).all()
        )


async def _message_rows(db: PgDatabase) -> list[Any]:
    """本会话的消息行 (按产生顺序)."""
    async with db.connect() as session:
        return list(
            session.execute(
                select(messages.c.run_id, messages.c.role, messages.c.content)
                .where(messages.c.thread_id == THREAD_ID)
                .order_by(messages.c.created_at)
            ).all()
        )


async def test_a_cli_resume_adds_one_run_row_and_points_the_segment_at_it(
    db: PgDatabase,
) -> None:
    """命令行续跑: 运行行**多一行**, 这一段落的帧与消息都指着它.

    从前这一段在记录层是**空白**的: 帧的 `run_id` 是 None (孤儿), 消息行一条没有
    —— 前端刷新看不到、成本记账记不上、轨迹串不起来. 这一条就是那个洞的回归.
    """
    saver = PostgresCheckpointSaver(engine=db.engine())
    recorder = _recorder(db)
    model = MockLLM.fixed(text_response("答了一次"))
    first = _session(model, saver, recorder)
    await first.ask("第一问")

    [before] = await _run_rows(db)

    # 同一个存储上的**另一个会话对象** —— 命令行 `--resume` 就是这个形状
    second = _session(model, saver, recorder)
    await second.resume()

    rows = await _run_rows(db)
    assert len(rows) == 2, f"应当只多一行 (新的一次运行), 实际 {len(rows)} 行"
    fresh = rows[-1]
    assert fresh.status == RunStatus.FINISHED.value, "这一次是它自己收的尾"

    frames = await saver.list_history(THREAD_ID)
    assert frames is not None
    assert {frame.run_id for frame in frames} == {before.run_id, fresh.run_id}, (
        "每一帧都该有归属 —— 从前续跑落的那些是 None (记录层里的孤儿)"
    )
    assert [frame.run_id for frame in frames].count(fresh.run_id) == 1, (
        "这一段落的帧指着新开的那一行"
    )

    rows_of_messages = await _message_rows(db)
    assert {row.run_id for row in rows_of_messages} == {
        before.run_id,
        fresh.run_id,
    }, "两段各自的消息都带上各自那一行"
    assert "答了一次" in [
        row.content for row in rows_of_messages if row.run_id == fresh.run_id
    ]


async def test_a_continuation_resume_reuses_the_run_and_settles_it(
    db: PgDatabase,
) -> None:
    """传了 `run_id` (HITL 的第二段): 不新增行, 帧与消息指回那一行, 并把结局写上去.

    第二段跑完了, 那一次运行也就结束了 —— 于是那一行落成终态 (这里是 finished),
    而**不是**像 ticket 33 当初写的那样「一笔都不碰」: 要碰, 但写的是**这一段的
    结局**, 不是「这一次运行结没结」(那件事由 `finished_at` 说).
    """
    saver = PostgresCheckpointSaver(engine=db.engine())
    recorder = _recorder(db)
    model = MockLLM.fixed(text_response("第一段答"))
    first = _session(model, saver, recorder)
    await first.ask("第一问")

    [finished] = await _run_rows(db)
    # 手工建一行「还开着」的运行: HITL 挂起时那一行就是这个样子 (真挂起那一路由
    # 下一条用例走, 这一条只关心「第二段往那一行写了什么」)
    opened = await RunsRepository(db).add(thread_id=THREAD_ID, status=RunStatus.RUNNING)

    second = _session(model, saver, recorder)
    await second.resume(run_id=opened.run_id)

    rows = await _run_rows(db)
    assert [row.run_id for row in rows] == [finished.run_id, opened.run_id], (
        "第二段不新开账"
    )
    [current] = [row for row in rows if row.run_id == opened.run_id]
    assert current.status == RunStatus.FINISHED.value, "这一段的结局写了回去"
    assert current.finished_at is not None
    assert current.turn_count == 2, (
        "账目是累计的 (计数器从快照接着数): 第一段问过模型一次, 第二段又问一次"
    )

    frames = await saver.list_history(THREAD_ID)
    assert frames is not None
    assert [frame.run_id for frame in frames] == [finished.run_id, opened.run_id], (
        "这一段落的帧指回**同一次运行**那一行"
    )

    continuation = [
        row for row in await _message_rows(db) if row.run_id == opened.run_id
    ]
    assert continuation, "第二段产生的消息也落在那一行下面"
    assert "第一段答" in [row.content for row in continuation]


# ---------------------------------------------------------------------------
# HITL 的两段 (issue 34): 挂起那一段写什么, 恢复那一段怎么把账收回来
# ---------------------------------------------------------------------------


@tool
def pay_order(order_no: str) -> str:
    """支付一笔订单.

    Args:
        order_no: 订单号.
    """
    return f"订单 {order_no} 支付成功"


def _gated_session(model, saver, recorder) -> ChatSession:
    """造一个「凡调用都要人批」的会话 (挂起点的最小形态)."""
    hooks = HookRegistry()
    hooks.register(
        HookPoint.BEFORE_TOOL_EXECUTE,
        lambda **kw: Decision.requires_approval(
            "这一单要付款了, 需要你输一次支付密码", needs=("payment_password",)
        ),
    )
    return ChatSession(
        model,
        saver=saver,
        tools=[pay_order],
        thread_id=THREAD_ID,
        model_name="deepseek-flash",
        recorder=recorder,
        hooks=hooks,
    )


async def _tool_call_rows(db: PgDatabase) -> list[Any]:
    """本会话的工具调用行 (按发起时刻)."""
    async with db.connect() as session:
        return list(
            session.execute(
                select(
                    tool_calls_table.c.run_id,
                    tool_calls_table.c.tool_name,
                    tool_calls_table.c.status,
                    tool_calls_table.c.approved_by,
                    tool_calls_table.c.approved_at,
                    tool_calls_table.c.approval_prompt,
                    tool_calls_table.c.approval_needs,
                )
                .where(tool_calls_table.c.run_id.in_(select(runs_table.c.run_id)))
                .order_by(tool_calls_table.c.created_at)
            ).all()
        )


async def test_a_suspension_settles_the_run_as_waiting_user(db: PgDatabase) -> None:
    """挂起那一段 (issue 34 的验收): 运行行落成 `waiting_user`, 结束时刻仍为空.

    三处一起看: 运行行 (还在等人) · 工具调用行 (`needs_approval` + 要问什么) ·
    帧 (挂着谁). 三者是同一件事的三个落点, 缺一个前端就重建不出那张卡.
    """
    saver = PostgresCheckpointSaver(engine=db.engine())
    recorder = _recorder(db)
    model = MockLLM.scripted(
        [tool_call_response(make_tool_call("pay_order", '{"order_no": "A1"}'))]
    )
    session = _gated_session(model, saver, recorder)

    result = await session.ask("帮我付了这一单")

    assert result.outcome is LoopOutcome.SUSPENDED

    [run] = await _run_rows(db)
    assert run.status == RunStatus.WAITING_USER.value, "挂起不是终态"
    assert run.finished_at is None, "那一次运行还开着"
    assert run.turn_count == 1

    [call] = await _tool_call_rows(db)
    assert call.status == ToolCallStatus.NEEDS_APPROVAL.value
    assert call.approved_by is None and call.approved_at is None
    assert call.approval_prompt == "这一单要付款了, 需要你输一次支付密码"
    assert call.approval_needs == ["payment_password"]

    frames = await saver.list_history(THREAD_ID)
    assert frames is not None
    suspension = frames[-1].state.suspension
    assert suspension is not None and suspension.reason == "needs_approval"
    assert [pending.name for pending in suspension.pending] == ["pay_order"]

    # 那条调用行的 `run_id` 与运行行对得上 (挂起那道闸门按会话 join 它)
    assert call.run_id == run.run_id


async def test_resuming_the_suspension_executes_it_and_closes_the_run(
    db: PgDatabase,
) -> None:
    """恢复那一段: 那条调用真的执行, 工具调用行推进到 succeeded, 运行行收成终态.

    同时钉住「同一次运行的第二段」这一条: 库里仍然只有**一行**运行, 而它的
    `finished_at` 是在第二段收尾时才写上的.
    """
    saver = PostgresCheckpointSaver(engine=db.engine())
    recorder = _recorder(db)
    suspended_model = MockLLM.scripted(
        [tool_call_response(make_tool_call("pay_order", '{"order_no": "A1"}'))]
    )
    first = _gated_session(suspended_model, saver, recorder)
    await first.ask("帮我付了这一单")
    [suspended] = await _run_rows(db)

    # 第二段: 换一个模型 (那一轮早就决策过了, 模型只会被问「付完之后怎么答」)
    second = _gated_session(MockLLM.fixed(text_response("已经付好了")), saver, recorder)
    result = await second.resume(run_id=suspended.run_id, approval=Approval.approve())

    assert result is not None and result.outcome is LoopOutcome.FINISHED

    rows = await _run_rows(db)
    assert [row.run_id for row in rows] == [suspended.run_id], "同一次运行, 没有新行"
    [finished] = rows
    assert finished.status == RunStatus.FINISHED.value
    assert finished.finished_at is not None, "第二段收尾时它才结的"

    [call] = await _tool_call_rows(db)
    assert call.status == ToolCallStatus.SUCCEEDED.value
    assert call.run_id == suspended.run_id, "补做的那条仍记在原来那一行上"
