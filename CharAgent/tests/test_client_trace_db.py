"""钱这条路的真库用例 (标 `pg_db`, 默认排除): **写进去**与**读出来**两头.

这一份覆盖 ticket 28 的两半, 分界很清楚:

- **写**: 记录员在运行收尾那一刻按当时的价目表算好, 写进 `charagent_runs` 的两列
  (`total_cost` / `total_cost_detail`). 用例里给的是**全天一价**的价目表 —— 峰谷那
  条路要「运行开始那一刻」, 而那一刻是记录员自己取当下的, 真库里没法钉死 (峰谷的
  判定规则本身在 `test_db_cost.py` 里逐条钉过了).
- **读**: `trace` 只读那两列 (本入口不再自己算钱), 打出来的就是行里那份事实.

三条**只写不改**的保证也在这里钉住 (用户点名要的那条): 金额写进去之后, 重复收尾、
状态推进都不许动它.

隔离与 `test_db_store.py` 一致: `charagent_test` schema, 用完整个删掉.
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from CharAgent.agent import LoopOutcome
from CharAgent.agent.utils.types import LoopResult
from CharAgent.client.trace import main
from CharAgent.db import (
    MessagesRepository,
    RunsRepository,
    RunStatus,
    ThreadsRepository,
    ToolCallsRepository,
    ToolCallStatus,
    visible_transcript,
)
from CharAgent.db.cost import PriceTable, RunCost
from CharAgent.db.database import PgDatabase
from CharAgent.db.errors import PricingNotReadyError
from CharAgent.db.recorder import ConversationRecorder
from CharAgent.db.schema import checkpoints, runs
from CharAgent.model import FinishReason

pytestmark = pytest.mark.pg_db

ORDER_ARGS = '{"order_no": "20260701123456"}'
# 全天一价那张表 (三档写法, 不用日历): 未命中 2 / 命中 0.5 / 输出 8 (每百万 token)
FLAT_PRICES = PriceTable.from_json(
    '{"models": {"deepseek-flash": {"cache_miss": 2, "cache_hit": 0.5, "output": 8}}}'
)
# 峰谷那张表 (用来验构造期的自检会拦住「判不了」的表)
SPLIT_PRICES = PriceTable.from_json(
    '{"timezone": "Asia/Shanghai", "peak_windows": [["09:00", "12:00"]],'
    ' "models": {"deepseek-flash": {"peak": {"cache_miss": 2, "cache_hit": 0.04,'
    ' "output": 8}, "valley": {"cache_miss": 1, "cache_hit": 0.02, "output": 4}}}}'
)


def _result(**usage) -> LoopResult:
    """一次跑完运行的结果 (只带账目那几项; 账由调用方给)."""
    return LoopResult(
        messages=[
            {"role": "user", "content": "我的订单到哪了"},
            {"role": "assistant", "content": "已发货"},
        ],
        content="已发货",
        finish_reason=FinishReason.STOP,
        outcome=LoopOutcome.FINISHED,
        turns=[],
        turn_count=1,
        prompt_ref=None,
        **usage,
    )


async def _seed_run(
    db: PgDatabase, *, model: str = "deepseek-flash"
) -> tuple[str, str]:
    """造一次跑完的运行 + 两次工具调用 (一次成功带结果, 一次失败带错误).

    这一份**不写金额** —— 它模拟的是「改口径之前写的行」与「没配价的行」, 用来验
    读的那一头 (金额那一列空着时屏幕上打什么).

    Returns:
        tuple[str, str]: (会话编号, 运行编号).
    """
    thread = await ThreadsRepository(db).add(tenant_id="t-trace", user_id="u-trace")
    run = await RunsRepository(db).add(
        thread_id=thread.thread_id, model=model, prompt_version="system/v1"
    )
    lines = await MessagesRepository(db).add_lines(
        thread_id=thread.thread_id,
        lines=visible_transcript(
            [
                {"role": "user", "content": "我的订单到哪了"},
                {
                    "role": "assistant",
                    "content": "让我查一下",
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "type": "function",
                            "function": {"name": "get_my_order", "arguments": "{}"},
                        }
                    ],
                },
            ]
        ),
        run_id=run.run_id,
    )
    message_id = lines[-1].message_id
    calls = ToolCallsRepository(db)
    await calls.add(
        run_id=run.run_id,
        message_id=message_id,
        tool_call_id="call_0",
        tool_name="get_my_order",
        arguments=ORDER_ARGS,
    )
    await calls.set_status(
        run.run_id,
        message_id,
        "call_0",
        ToolCallStatus.SUCCEEDED,
        result="订单已发货, 单号 SF123",
        duration_ms=142,
    )
    await calls.add(
        run_id=run.run_id,
        message_id=message_id,
        tool_call_id="call_1",
        tool_name="request_refund",
        arguments=ORDER_ARGS,
    )
    await calls.set_status(
        run.run_id,
        message_id,
        "call_1",
        ToolCallStatus.FAILED,
        result="这一单已经申请过退款",
        duration_ms=31,
    )
    await RunsRepository(db).settle(
        run.run_id,
        status=RunStatus.FINISHED,
        model=model,
        prompt_version="system/v1",
        turn_count=2,
        total_tokens=459,
        input_tokens=400,
        output_tokens=59,
        reasoning_tokens=16,
        cache_hit_tokens=0,
        cache_miss_tokens=400,
    )
    return thread.thread_id, run.run_id


async def money_of(db: PgDatabase, run_id: str) -> tuple[Decimal | None, dict | None]:
    """把某一行那两列读回来 (直接查表: 断言的是**库里存了什么**)."""
    async with db.connect() as session:
        row = session.execute(
            select(runs.c.total_cost, runs.c.total_cost_detail).where(
                runs.c.run_id == run_id
            )
        ).one()
    return row.total_cost, row.total_cost_detail


async def trace_output(argv: list[str], database: PgDatabase) -> tuple[int, str]:
    """跑一次 `trace` 入口, 返回 (退出码, 打出来的全文).

    **借一个工作线程跑**: `main` 是同步入口 (自己 `asyncio.run` 开循环), 而
    pytest-asyncio 的用例本身就跑在事件循环里 —— 直接调会拿到「asyncio.run() cannot
    be called from a running event loop」. 换个线程等于还它一个「终端进程」的环境.
    """
    printed: list[str] = []
    code = await asyncio.to_thread(main, argv, database=database, writer=printed.append)
    return code, "\n".join(printed)


# ---------------------------------------------------------------------------
# 写: 收尾那一刻把金额算好落库
# ---------------------------------------------------------------------------


async def test_a_finished_run_gets_its_money_written(db):
    """收尾那一拍算好写进去: 金额 + 算式都在行里 (trace 之后只读它).

    这一条是 ticket 28 改判的正面证据: 钱不再等到有人查的时候才算.
    """
    thread = await ThreadsRepository(db).add(tenant_id="t-1", user_id="u-1")
    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=FLAT_PRICES
    )
    run_id = await recorder.begin(thread_id=thread.thread_id)
    assert run_id is not None

    ok = await recorder.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(
            total_tokens=459,
            input_tokens=400,
            output_tokens=59,
            reasoning_tokens=16,
            cache_hit_tokens=0,
            cache_miss_tokens=400,
        ),
        model="deepseek-flash",
    )

    total, detail = await money_of(db, run_id)
    assert ok
    # 未命中 400 x 2/M + 命中 0 + 输出 59 x 8/M = 0.0008 + 0.000472
    assert total == Decimal("0.001272")
    assert detail["kind"] == "cost"
    assert detail["tier"] is None, "全天一价的模型没有档位"
    assert [line["tier"] for line in detail["lines"]] == [
        "cache_miss",
        "cache_hit",
        "output",
    ]
    assert detail["lines"][2]["tokens"] == 59


async def test_a_run_without_prices_has_no_amount_but_a_reason(db):
    """没配价目表 -> 金额留 NULL, 明细写清为什么 (绝不写 0)."""
    thread = await ThreadsRepository(db).add(tenant_id="t-1", user_id="u-1")
    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=PriceTable()
    )
    run_id = await recorder.begin(thread_id=thread.thread_id)

    await recorder.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(total_tokens=100, input_tokens=100, output_tokens=0),
        model="没配过的模型",
    )

    total, detail = await money_of(db, run_id)
    assert total is None, "没有金额就是 NULL —— 0 会被读成「没花钱」"
    assert detail["kind"] == "gap"
    assert detail["reason"] == "no_price"
    assert detail["model"] == "没配过的模型"


async def test_an_unfinished_run_has_no_amount_and_says_why(db):
    """没跑完那一轮 (取消 / 失败): 没有账目, 也就没有金额 —— 明细记一句原因."""
    thread = await ThreadsRepository(db).add(tenant_id="t-1", user_id="u-1")
    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=FLAT_PRICES
    )
    run_id = await recorder.begin(thread_id=thread.thread_id)

    await recorder.record_unfinished(
        thread_id=thread.thread_id,
        question="我的订单到哪了",
        status=RunStatus.CANCELLED,
        run_id=run_id,
        model="deepseek-flash",
    )

    total, detail = await money_of(db, run_id)
    assert total is None
    assert detail["reason"] == "unfinished"


async def test_the_money_written_once_is_never_overwritten(db):
    """**金额写进去就不许再被改** (用户点名要的那条保证).

    第二拍给一份算不出来的情况 (价目表空了): 金额那一列一个字节都不动 —— 既不是
    被写成 0, 也不是被清成 NULL. 明细也不动 (它还记着算式, 与金额自洽).
    """
    thread = await ThreadsRepository(db).add(tenant_id="t-1", user_id="u-1")
    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=FLAT_PRICES
    )
    run_id = await recorder.begin(thread_id=thread.thread_id)
    usage = {
        "total_tokens": 459,
        "input_tokens": 400,
        "output_tokens": 59,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 400,
    }
    await recorder.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(**usage),
        model="deepseek-flash",
    )
    before = await money_of(db, run_id)

    # 第二拍: 同一行再落定一次, 而这次算不出来 (换个没有价的记录员)
    blind = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=PriceTable()
    )
    await blind.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(**usage),
        model="deepseek-flash",
    )

    assert await money_of(db, run_id) == before


async def test_a_second_finish_recomputes_from_the_columns(db):
    """第二拍**算得出来**时: 按当前列重算并覆盖 —— 不是累加, 也不是「再也不许改」.

    这是那条规则的另一半 (上一條钉的是「算不出来时不动它」): 用量那五列是**累计
    值**, 所以重算出来的就是那一刻的总额; 累加会把前面那段算两次. 真出现「同一行
    两次收尾」时 (今天没有这条路径), 这一条与上一条合起来就是完整口径.
    """
    thread = await ThreadsRepository(db).add(tenant_id="t-1", user_id="u-1")
    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=FLAT_PRICES
    )
    run_id = await recorder.begin(thread_id=thread.thread_id)
    await recorder.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(
            total_tokens=100,
            input_tokens=100,
            output_tokens=0,
            cache_hit_tokens=0,
            cache_miss_tokens=100,
        ),
        model="deepseek-flash",
    )
    first, _ = await money_of(db, run_id)

    # 第二拍: 同样的账再落一次 (用量列没变) -> 金额还是那个数 (干净的重算)
    await recorder.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(
            total_tokens=100,
            input_tokens=100,
            output_tokens=0,
            cache_hit_tokens=0,
            cache_miss_tokens=100,
        ),
        model="deepseek-flash",
    )
    again, _ = await money_of(db, run_id)

    assert again == first, "同一份账重算两次得到同一个数 (不是相加)"


async def test_a_later_state_write_does_not_touch_the_money(db):
    """别的写路径 (改状态那些) 都不碰金额那一列 —— 「谁都不许动它」钉在这里.

    拿 `set_status` 当代表 (它是**对账 / 修数据**那条路: 不看当前状态, 想改就改),
    而不是 `try_transition` —— 后者带乐观锁 (WHERE status = 我以为的那个), 事后的
    运行已经是终态, 它本来就改不动, 拿它测会测出「什么都没发生」这种假证据.
    """
    thread = await ThreadsRepository(db).add(tenant_id="t-1", user_id="u-1")
    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=FLAT_PRICES
    )
    run_id = await recorder.begin(thread_id=thread.thread_id)
    await recorder.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(
            total_tokens=459,
            input_tokens=400,
            output_tokens=59,
            cache_hit_tokens=0,
            cache_miss_tokens=400,
        ),
        model="deepseek-flash",
    )
    before = await money_of(db, run_id)

    runs_repo = RunsRepository(db)
    assert await runs_repo.set_status(run_id, RunStatus.FAILED, error={"code": "x"})
    assert await runs_repo.set_status(run_id, RunStatus.FINISHED)

    assert await money_of(db, run_id) == before


async def test_the_recorder_refuses_to_start_on_a_broken_config(db):
    """配置写错 / 日历判不了 -> 记录员构造就抛 (启动自检那条路的真库版本)."""
    with pytest.raises(PricingNotReadyError):
        ConversationRecorder(
            database=db,
            tenant_id="t-1",
            user_id="u-1",
            prices=PriceTable(error="价目表里 'm' 少了这些档: output"),
        )


async def test_the_recorder_refuses_a_calendar_it_cannot_use(db, monkeypatch):
    """配了峰谷价但日历依赖不在 -> 也抛 (报错里给安装命令)."""
    monkeypatch.setitem(sys.modules, "chinese_calendar", None)

    with pytest.raises(PricingNotReadyError) as info:
        ConversationRecorder(
            database=db, tenant_id="t-1", user_id="u-1", prices=SPLIT_PRICES
        )

    assert "chinesecalendar" in str(info.value)


async def test_a_flat_table_does_not_need_the_calendar(db, monkeypatch):
    """只配全天一价的部署, 日历不在也照样起得来 (用不上的东西不该拦住)."""
    monkeypatch.setitem(sys.modules, "chinese_calendar", None)

    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=FLAT_PRICES
    )

    assert recorder is not None


# ---------------------------------------------------------------------------
# 读: trace 把行里那份事实摆出来
# ---------------------------------------------------------------------------


async def test_trace_reads_the_money_from_the_row(db):
    """库里写了金额 -> `trace` 原样打出来 (算式也是行里那份, 不重算)."""
    thread = await ThreadsRepository(db).add(tenant_id="t-1", user_id="u-1")
    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=FLAT_PRICES
    )
    run_id = await recorder.begin(thread_id=thread.thread_id)
    await recorder.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(
            total_tokens=459,
            input_tokens=400,
            output_tokens=59,
            cache_hit_tokens=0,
            cache_miss_tokens=400,
        ),
        model="deepseek-flash",
    )

    code, text = await trace_output([run_id], db)

    assert code == 0
    assert "金额 ¥0.001272" in text
    assert "└ cache_miss 400 x ¥2/M + cache_hit 0 x ¥0.5/M + output 59 x ¥8/M" in text


async def test_trace_says_why_when_there_is_no_money(db):
    """没有金额的老行 (改口径之前写的) -> 说清「没有」与原因, 不打 ¥0."""
    thread_id, run_id = await _seed_run(db)

    code, text = await trace_output([run_id], db)

    assert code == 0
    assert f"会话 {thread_id} (租户 t-trace / 用户 u-trace)" in text
    assert "工具调用 (2 次)" in text
    assert "get_my_order" in text and "request_refund" in text
    assert ORDER_ARGS in text, "参数要原样打出来"
    assert "└ 结果: 订单已发货, 单号 SF123" in text
    assert "└ 结果: 这一单已经申请过退款" in text
    assert "142ms" in text and "31ms" in text
    amount = next(line for line in text.splitlines() if "金额" in line)
    assert "¥" not in amount, "没有金额时不能给一个数 (哪怕是 0)"
    assert "没有" in amount


async def test_trace_needs_no_pricing_configuration(db, monkeypatch):
    """`trace` 不读任何价目表配置: 环境变量清空也照样打得出来 (它只读库)."""
    monkeypatch.delenv("CHARAGENT_MODEL_PRICES", raising=False)
    _, run_id = await _seed_run(db)

    code, text = await trace_output([run_id], db)

    assert code == 0
    assert "工具调用 (2 次)" in text


async def test_main_reports_an_unknown_run_id(db):
    """库里没有这个编号 -> 说清楚, 退出码 1 (不是打半张空表)."""
    code, text = await trace_output(["没有这个运行"], db)

    assert code == 1
    assert "没有这次运行" in text


async def test_view_tier_reads_the_frames_view(db):
    """`--view` 把帧里那份 metadata.view 读出来 (那一轮真的发出去的是什么)."""
    thread_id, run_id = await _seed_run(db)
    async with db.connect() as session:
        session.execute(
            checkpoints.insert().values(
                checkpoint_id="ck-trace-1",
                thread_id=thread_id,
                run_id=run_id,
                loop_id="loop-trace-1",
                turn_number=0,
                schema_version=8,
                state={},
                metadata={
                    "view": {
                        "estimated_tokens": 410,
                        "estimate_drift": -49,
                        "cache_hit_ratio": 0.25,
                        "dropped": 2,
                        "truncated": 0,
                        "reasoning_cleared": 0,
                        "saved_tokens": 88,
                        "summarized": True,
                        "messages": [{"role": "user"}, {"role": "tool"}],
                    }
                },
            )
        )
    code, text = await trace_output([run_id, "--view"], db)

    assert code == 0
    assert "帧视图 (1 帧;" in text
    assert "  轮 0  估算 410 tok (漂移 -49) · 命中率 0.25 · " in text
    assert (
        "裁掉 2 条 / 截短 0 条 / 清思维链 0 段 (省 88) · 已更新摘要 · 视图 2 条" in text
    )


async def test_view_tier_is_not_printed_without_the_flag(db):
    """不加 `--view` 就不去读帧 (默认输出只管「干了什么、花了多少」)."""
    _, run_id = await _seed_run(db)

    code, text = await trace_output([run_id], db)

    assert code == 0
    assert "帧视图" not in text


async def test_trace_reads_the_database_that_was_injected(db):
    """注入进来的连接就是它读的那个 (而不是偷偷自己开一个 PgDatabase)."""
    _, run_id = await _seed_run(db)

    code, text = await trace_output([run_id], db)

    assert code == 0
    assert f"run  {run_id}" in text


async def test_the_run_row_keeps_its_detail_in_json(db):
    """明细真的落在 JSONB 那一列里 (能用 SQL 取字段出来对账)."""
    thread = await ThreadsRepository(db).add(tenant_id="t-1", user_id="u-1")
    recorder = ConversationRecorder(
        database=db, tenant_id="t-1", user_id="u-1", prices=FLAT_PRICES
    )
    run_id = await recorder.begin(thread_id=thread.thread_id)
    await recorder.record(
        thread_id=thread.thread_id,
        run_id=run_id,
        result=_result(
            total_tokens=459,
            input_tokens=400,
            output_tokens=59,
            cache_hit_tokens=0,
            cache_miss_tokens=400,
        ),
        model="deepseek-flash",
    )

    async with db.connect() as session:
        tier = session.execute(
            text(
                "select total_cost_detail ->> 'kind' from charagent_runs"
                " where run_id = :r"
            ),
            {"r": run_id},
        ).scalar()

    assert tier == "cost"
    assert RunCost.from_detail((await money_of(db, run_id))[1]).known
