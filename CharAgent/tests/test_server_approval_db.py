"""审批那条路落在**真库**里的样子 (标 `pg_db`, 默认排除).

`test_server_approval.py` 用假库把路由与闸门的形状跑通, 而假库按设计**不做 SQL**
(见 doubles.py) —— 于是有四件事只有真库才验得了, 本文件专管它们:

| 只有真库能验的 | 为什么 |
|---|---|
| 「这段会话挂着吗」那条查询 (含 join 回会话) | 假库不过滤, 也不认识 join |
| 「未决」那两列的语义 (`approved_at IS NULL`) | 同上: 它是 SQL 里的条件 |
| 幂等键落在 `charagent_idempotency_keys` 上 | 认领是一条带 `ON CONFLICT` |
| | 的语句 (`PgIdempotencyStore`) |
| **进程重启之后仍然拦得住** | 闸门的那一半判据在库里, 不在内存 —— |
| | 这一条只用真库才说得清 |

装配与业务侧同形: 真 `create_app` + 真快照存储 (Postgres) + 真记录员
(`ConversationRecorder`) + 真幂等登记簿, 只有模型是替身. 会话那份装配在**每次
恢复时重新做一遍** (一次性载荷要进工具闭包), 所以存储必须是进程级共享的 —— 与
真实装配同形.

隔离与其余 pg_db 用例一致: `charagent_test` schema, 用完整个删掉.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response
from sqlalchemy import select

from CharAgent.agent import RunContext
from CharAgent.checkpoint import PostgresCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.db import (
    ConversationRecorder,
    PgDatabase,
    RunStatus,
    ToolCallStatus,
)
from CharAgent.db.cost import PriceTable
from CharAgent.db.repositories.tool_calls import ToolCallsRepository
from CharAgent.db.schema import runs as runs_table
from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.model.protocol import ChatModel
from CharAgent.retry.idempotency import IdempotencyKey
from CharAgent.server import ServerAuthError, create_app
from CharAgent.server.history import PENDING_APPROVAL_FIELD
from CharAgent.tool import Tool, tool

pytestmark = pytest.mark.pg_db

TOKEN = "toy-token"
THREAD = "approval-db:u-1:chat-1"
PROMPT = "这一单要付款了, 需要你输一次支付密码"
FLAT_PRICES = PriceTable.from_json(
    '{"models": {"deepseek-flash": {"cache_miss": 2, "cache_hit": 0.5, "output": 8}}}'
)

# 工具真的跑了几次 (挂起与恢复的关键证据)
CALLS: list[str] = []


@tool(annotations={"high_risk": True})
def pay_order(order_no: str) -> str:
    """支付一笔订单.

    Args:
        order_no: 订单号.
    """
    CALLS.append(order_no)
    return f"订单 {order_no} 支付成功"


class ToyContexts:
    """插座一: 认证 + 解析 (只认自己的头)."""

    async def provide(self, request: httpx.Request) -> RunContext:
        if request.headers.get("X-Toy-Token") != TOKEN:
            raise ServerAuthError()
        return RunContext(
            thread_id=request.headers.get("X-Thread-Id", THREAD),
            tenant_id="toy",
            user_id=request.headers.get("X-Toy-User", "alice"),
            payload={},
        )


class PgSessions:
    """插座二: 装配 (共享一个 Postgres 快照存储 + 真记录员 + 挂核查插件).

    存储与记录员都是**进程级**的 (与真实装配同形): 恢复时框架会重新装配一次会话,
    而新会话必须看得见同一份快照 —— 各自一个存储就等于每次恢复都读不到存档.
    """

    def __init__(self, db: PgDatabase, model: ChatModel) -> None:
        self.db = db
        self.model = model
        self.saver = PostgresCheckpointSaver(engine=db.engine())
        self.recorder = ConversationRecorder(
            database=db, tenant_id="toy", user_id="alice", prices=FLAT_PRICES
        )
        self.sessions: list[ChatSession] = []

    async def provide(self, context: RunContext, *, event_sink) -> ChatSession:
        """装配 (每次恢复都会再调一次 —— 见模块 docstring)."""
        hooks = HookRegistry()
        hooks.register(HookPoint.BEFORE_TOOL_EXECUTE, self._gate)
        session = ChatSession(
            self.model,
            saver=self.saver,
            tools=[pay_order],
            thread_id=context.thread_id,
            model_name="deepseek-flash",
            event_sink=event_sink,
            hooks=hooks,
            recorder=self.recorder,
        )
        self.sessions.append(session)
        return session

    @staticmethod
    def _gate(**kwargs: Any) -> Decision | None:
        """核查插件: 高危工具要人工确认."""
        target: Tool = kwargs["tool"]
        if target.annotations.get("high_risk"):
            return Decision.requires_approval(PROMPT, needs=("payment_password",))
        return None


def make_server(db: PgDatabase, model: ChatModel):
    """起一个真库上的服务 (返回 app 与那两个插座).

    每调一次就是**一套全新的进程内状态** (登记表 / 会话 / 装配), 而库是传进来的
    那一个 —— 「重启」那条用例靠的正是这一点.
    """
    provider = PgSessions(db, model)
    app = create_app(
        context_provider=ToyContexts(), session_provider=provider, database=db
    )
    return app, provider


def client_for(app) -> httpx.AsyncClient:
    """ASGI 直连客户端 (不开端口, 不起服务)."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://toy"
    )


def headers(*, thread: str = THREAD, user: str = "alice", token: str = TOKEN) -> dict:
    """业务自己那套头 (框架一个都不认识)."""
    return {"X-Toy-Token": token, "X-Toy-User": user, "X-Thread-Id": thread}


def suspended_model(order_no: str = "A1") -> MockLLM:
    """两轮脚本: 第一轮要付钱 (于是挂起), 第二轮是恢复那一段请它作答."""
    return MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("pay_order", f'{{"order_no": "{order_no}"}}')
            ),
            text_response("已经付好了"),
        ]
    )


async def ask(app, message: str, **kwargs: Any) -> httpx.Response:
    """POST /runs."""
    async with client_for(app) as client:
        return await client.post(
            "/runs", json={"message": message}, headers=headers(**kwargs)
        )


async def resume(app, run_id: str, body: dict, **kwargs: Any) -> httpx.Response:
    """POST /runs/{id}/resume."""
    async with client_for(app) as client:
        return await client.post(
            f"/runs/{run_id}/resume", json=body, headers=headers(**kwargs)
        )


async def cancel(app, run_id: str, **kwargs: Any) -> httpx.Response:
    """POST /runs/{id}/cancel."""
    async with client_for(app) as client:
        return await client.post(f"/runs/{run_id}/cancel", headers=headers(**kwargs))


async def history(app, **kwargs: Any) -> dict:
    """GET /history."""
    async with client_for(app) as client:
        response = await client.get("/history", headers=headers(**kwargs))
    assert response.status_code == 200, response.text
    return response.json()


def parse_sse(body: str) -> list[dict[str, Any]]:
    """SSE 正文 → 逐事件."""
    events: list[dict[str, Any]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append({"event": lines["event"], "data": json.loads(lines["data"])})
    return events


async def wait_until(predicate, *, timeout: float = 2.0) -> None:
    """等一个条件成立 (轮询 + 超时)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "等待超时: 条件一直没成立"
        await asyncio.sleep(0.01)


async def suspend_once(app, message: str = "帮我付了这一单") -> str:
    """跑到挂起, 返回那次运行的编号 (从确认卡那一块读)."""
    response = await ask(app, message)
    assert response.status_code == 200, response.text
    assert parse_sse(response.text)[-1]["event"] == "approval_required"
    block = (await history(app))[PENDING_APPROVAL_FIELD]
    assert block is not None, "挂起之后 /history 该带着确认卡的信息"
    return block["run_id"]


async def wait_for_stamp(db: PgDatabase, run_id: str) -> None:
    """等「谁批的」那一对列写下去 (它在任务的收尾回调里, 比事件流晚一小步)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    calls = ToolCallsRepository(db)
    while loop.time() < deadline:
        [call] = await calls.list_for_run(run_id)
        if call.approved_at is not None:
            return
        await asyncio.sleep(0.01)
    pytest.fail("「谁批的」那一对列一直没写上")


async def _run_rows(db: PgDatabase) -> list[Any]:
    """本会话的运行行."""
    async with db.connect() as session:
        return list(
            session.execute(
                select(
                    runs_table.c.run_id,
                    runs_table.c.status,
                    runs_table.c.finished_at,
                ).where(runs_table.c.thread_id == THREAD)
            ).all()
        )


# ---------------------------------------------------------------------------
# 全流程 (真 SQL)
# ---------------------------------------------------------------------------


async def test_the_whole_flow_over_real_sql(db: PgDatabase) -> None:
    """挂起 → 刷新拿得到卡 → 批准 → 那条调用执行、运行收尾、卡消失.

    五处断言各自对应一个**只有真库才验得了**的东西: 未决那条查询 (含 join)、
    「未决」那两列 (状态 + `approved_at IS NULL`)、卡消失 (同一查询的否定面)、
    工具调用行推进到终态、运行行收成终态.
    """
    CALLS.clear()
    app, _ = make_server(db, suspended_model())

    run_id = await suspend_once(app)

    assert CALLS == [], "挂起时那条调用一次都没跑"
    block = (await history(app))[PENDING_APPROVAL_FIELD]
    assert block is not None
    assert (block["tool_name"], block["prompt"]) == ("pay_order", PROMPT)
    assert block["needs"] == ["payment_password"]

    response = await resume(
        app, run_id, {"decision": "approve", "data": {"payment_password": "pw-123"}}
    )

    assert response.status_code == 200, response.text
    assert parse_sse(response.text)[-1]["event"] == "final"
    assert CALLS == ["A1"], "批准之后那条调用真的执行了"
    cleared = (await history(app))[PENDING_APPROVAL_FIELD]
    assert cleared is None, "卡该消失了 (真 SQL 筛过)"

    # 「谁批的」那一对列写在**运行收尾之后** (见 app.py 的 ApprovalBookkeeping),
    # 所以这里等它一小步 (等的是库里那一列非空)
    await wait_for_stamp(db, run_id)

    [call] = await ToolCallsRepository(db).list_for_run(run_id)
    assert call.status == ToolCallStatus.SUCCEEDED.value
    assert call.approved_by == "alice"
    assert call.approval_prompt == PROMPT, "话术还在那一行上 (审计得到)"
    [run] = await _run_rows(db)
    assert run.status == RunStatus.FINISHED.value
    assert run.finished_at is not None


async def test_a_restart_still_refuses_a_new_question(db: PgDatabase) -> None:
    """**重启之后仍然拦得住** —— 闸门的判据在库里, 不在内存 (issue 34 的验收).

    造法: 挂起之后换一套全新的登记表与会话 (那个进程的内存全丢了), 而库还是同一
    个. 新提问照旧 409 —— 拿内存集合当判据的话, 这一条会当场放行, 而放行的后果是
    新的一句问话与一次未决的确认撞在同一份存档上.
    """
    CALLS.clear()
    first_app, _ = make_server(db, suspended_model())
    await suspend_once(first_app)

    # 「重启」= 新的 app / 新的登记表 / 新的会话, 库不变
    restarted, _ = make_server(db, MockLLM.fixed(text_response("答一下")))

    refused = await ask(restarted, "那算了, 换个问题")

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "thread_suspended"
    assert CALLS == [], "被拦下的提问不会跑到工具那儿去"

    # 而恢复那条路照常放行 (它是解决这次挂起的动作)
    run_id = (await history(restarted))[PENDING_APPROVAL_FIELD]["run_id"]
    approved = await resume(restarted, run_id, {"decision": "approve"})
    assert approved.status_code == 200
    assert CALLS == ["A1"]


async def test_another_conversation_cannot_touch_the_suspension(db: PgDatabase) -> None:
    """别人的会话既取消不了也恢复不了 (判据是这次请求认出来的那段会话).

    这一条非要真库不可: 假库不做 join —— 而「挂起属不属于这段会话」正是 join 出来
    的 (工具调用表只有 `run_id`, 会话是运行行的属性).
    """
    CALLS.clear()
    app, _ = make_server(db, suspended_model())
    run_id = await suspend_once(app)

    denied = await resume(
        app, run_id, {"decision": "approve"}, thread="approval-db:u-2:chat-1"
    )
    assert denied.status_code == 404
    denied_cancel = await cancel(app, run_id, thread="approval-db:u-2:chat-1")
    assert denied_cancel.status_code == 404
    assert CALLS == [], "越界的那两条一条都不该产生效果"
    assert (await history(app))[PENDING_APPROVAL_FIELD] is not None, "还挂着"


async def test_the_idempotency_key_really_lands_in_the_table(db: PgDatabase) -> None:
    """幂等键真的写进 `charagent_idempotency_keys`, 而第二次恢复被它挡住.

    与离线那份的分工: 那边用进程内登记簿验「挡住了」, 这边验的是**实现**: 认领走的
    是一条带 `ON CONFLICT` 的语句, 而那一行确实落在库里 (跨进程的那一半).
    """
    CALLS.clear()
    app, _ = make_server(db, suspended_model())
    run_id = await suspend_once(app)
    [pending] = await ToolCallsRepository(db).list_pending_approvals(THREAD)
    # 键的拼法只有一处实现 (`server/app.py` 的 `_approval_key`, 私有): 这里按那个
    # 拼法复写一份 —— **故意钉住**它, 格式一变这条用例当场红
    key = IdempotencyKey(
        f"resume:{pending.run_id}:{pending.message_id}:{pending.tool_call_id}"
    )

    assert (await resume(app, run_id, {"decision": "approve"})).status_code == 200
    assert CALLS == ["A1"]

    # 同一把键再认领: 已经不是「首次」了 (认领过的键落在库里, 跨进程也拦得住)
    from CharAgent.db import PgIdempotencyStore
    from CharAgent.retry.utils.types import ClaimStatus

    refreshed = PgIdempotencyStore(db)  # 换一个实例 = 换一个「进程」
    assert (await refreshed.claim(key)).status is not ClaimStatus.CLAIMED
