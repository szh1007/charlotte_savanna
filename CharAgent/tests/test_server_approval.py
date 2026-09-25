"""审批那条路 (issue 34): 挂起 → 确认卡 → 恢复, 走 HTTP 端到端.

被测的是**服务层那一圈** (loop 那一侧归 `test_loop_suspension.py`, 真库的行归
`test_server_approval_db.py`):

| 场景 | 断言 |
|---|---|
| 一次要人批的调用 | 事件流以 `approval_required` 收尾 (终局), 运行落在 `waiting_user` |
| 刷新页面 | `GET /history` 带回可重建确认卡的四样 (含 `run_id`) |
| 批准 | 那条调用真的执行, 未决挂起消失 (再刷新就拿不到卡了) |
| 拒绝 | 不执行, 原因当工具结果回填, 模型继续答 |
| 一次性载荷 | 恢复请求里的 `data` 到得了工具手里 (进的是**重新装配**那次会话) |
| 重复提交 | 第二次 409 (在办 / 已办), 不是再执行一遍 |
| 没有挂起 | 404 (与取消那条同源: 几种「不在」不区分) |
| 未决期间 | 新提问 409 (`thread_suspended`, 与「会话忙」码不同); |
| | `resume` / `cancel` 放行 |
| 取消挂起 | 那一行收成 `cancelled`, 闸门跟着放开 (新提问又能提了) |
| 没配库 | 这条路由**不注册** (404) |

装配: 真 `create_app` + 假库 (`PendingAwareDatabase` —— 在 `FakeRecordDatabase`
之上补了一条「按未决筛」的语义, 见 doubles.py) + 进程内幂等登记簿 + 真记录员
(`ConversationRecorder` 写进那个假库) + 内存快照存储 (三处会话共用一份 —— 与真实
装配同形: 存储是进程级的). 模型走 `MockLLM`, 零网络零外部服务.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from doubles import PendingAwareDatabase
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response
from starlette.applications import Starlette

from CharAgent.agent import RunContext
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.db import ConversationRecorder, Database, RunStatus
from CharAgent.db.cost import PriceTable
from CharAgent.db.entities import ToolCallStatus
from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.model.protocol import ChatModel
from CharAgent.retry.idempotency import IdempotencyKey, InMemoryIdempotencyStore
from CharAgent.server import ServerAuthError, create_app
from CharAgent.server.history import (
    APPROVAL_NEEDS_FIELD,
    APPROVAL_PROMPT_FIELD,
    APPROVAL_RUN_ID_FIELD,
    APPROVAL_TOOL_CALL_ID_FIELD,
    APPROVAL_TOOL_NAME_FIELD,
    MESSAGES_FIELD,
    PENDING_APPROVAL_FIELD,
)
from CharAgent.tool import Tool, tool

TOKEN = "toy-token"
THREAD = "toy:chat-1"
PROMPT = "这一单要付款了, 需要你输一次支付密码"
# 全天一价那张表 (记录员构造时要一张好表; 金额本身不是这一页的事)
FLAT_PRICES = PriceTable.from_json(
    '{"models": {"deepseek-flash": {"cache_miss": 2, "cache_hit": 0.5, "output": 8}}}'
)

# 工具真的跑了几次、看到了什么 (挂起与恢复的关键证据)
CALLS: list[str] = []


def make_tools(context: RunContext) -> Sequence[Tool]:
    """按上下文装工具 (身份与一次性载荷都进闭包) —— 与业务侧同一套做法.

    支付工具打了 `high_risk` 标记, 于是核查插件会对它要人工确认; 它还把**载荷**里
    那件东西记下来: 一次性载荷有没有真的到工具手里, 只有它能证明.
    """

    @tool(annotations={"high_risk": True})
    def pay_order(order_no: str) -> str:
        """支付一笔订单.

        Args:
            order_no: 订单号.
        """
        secret = context.payload.get("payment_password", "(没有)")
        CALLS.append(f"pay:{order_no}:{secret}")
        return f"订单 {order_no} 支付成功"

    @tool
    def look_up(order_no: str) -> str:
        """查询订单状态.

        Args:
            order_no: 订单号.
        """
        CALLS.append(f"look:{order_no}")
        return "已发货"

    return (pay_order, look_up)


class ToyContexts:
    """插座一: 认证 + 解析 (只认自己的头, 与框架无关)."""

    async def provide(self, request: httpx.Request) -> RunContext:
        if request.headers.get("X-Toy-Token") != TOKEN:
            raise ServerAuthError()
        return RunContext(
            thread_id=request.headers.get("X-Thread-Id", THREAD),
            tenant_id="toy",
            user_id=request.headers.get("X-Toy-User", "alice"),
            payload={},
        )


class ToySessions:
    """插座二: 装配 (共享一个快照存储 + 每次现装工具 + 挂核查插件).

    存储**进程级共享** (与真实装配同形): 恢复时框架会重新装配一次会话 (一次性的
    载荷要进这次的工具闭包), 而新会话必须看得见同一份快照 —— 各自一个存储就等于
    每次恢复都读不到存档.
    """

    def __init__(
        self,
        model: ChatModel,
        *,
        database: Database | None,
        tools_for: Callable[[RunContext], Sequence[Tool]] = make_tools,
    ) -> None:
        self.model = model
        self.tools_for = tools_for
        self.saver = InMemoryCheckpointSaver()
        self.sessions: list[ChatSession] = []
        self.recorder = (
            None
            if database is None
            else ConversationRecorder(
                database=database, tenant_id="toy", user_id="alice", prices=FLAT_PRICES
            )
        )

    async def provide(self, context: RunContext, *, event_sink) -> ChatSession:
        """装配 (每次恢复都会再调一次: 这次运行带的载荷要进工具闭包)."""
        hooks = HookRegistry()
        hooks.register(HookPoint.BEFORE_TOOL_EXECUTE, self._gate)
        session = ChatSession(
            self.model,
            saver=self.saver,
            tools=list(self.tools_for(context)),
            thread_id=context.thread_id,
            event_sink=event_sink,
            hooks=hooks,
            recorder=self.recorder,
        )
        self.sessions.append(session)
        return session

    @staticmethod
    def _gate(**kwargs: Any) -> Decision | None:
        """核查插件: 高危工具要人工确认 (按工具自己的注解表态, 框架只透传)."""
        target: Tool = kwargs["tool"]
        if target.annotations.get("high_risk"):
            return Decision.requires_approval(PROMPT, needs=("payment_password",))
        return None


class ToyServer:
    """一整套玩具服务 (app + 两个插座 + 那个假库)."""

    def __init__(
        self,
        model: ChatModel,
        *,
        database: Database | None,
        guard=None,
        tools_for: Callable[[RunContext], Sequence[Tool]] = make_tools,
    ) -> None:
        self.contexts = ToyContexts()
        self.sessions = ToySessions(model, database=database, tools_for=tools_for)
        self.database = database
        self.guard = InMemoryIdempotencyStore() if guard is None else guard
        self.app: Starlette = create_app(
            context_provider=self.contexts,
            session_provider=self.sessions,
            database=database,
            idempotency=self.guard,
        )

    @property
    def registry(self):
        """会话登记表 (断言「闸门开了没」用)."""
        return self.app.state.session_registry


def serve(
    model: ChatModel,
    *,
    database: Database | None = None,
    guard: Any = None,
    tools_for: Callable[[RunContext], Sequence[Tool]] = make_tools,
) -> ToyServer:
    """起一个玩具服务 (默认带一个假库 —— 审批那条路要它才有挂起态的家)."""
    return ToyServer(
        model,
        database=PendingAwareDatabase() if database is None else database,
        guard=guard,
        tools_for=tools_for,
    )


def headers(*, thread: str = THREAD, user: str = "alice", token: str = TOKEN) -> dict:
    """业务自己那套头 (框架一个都不认识)."""
    return {"X-Toy-Token": token, "X-Toy-User": user, "X-Thread-Id": thread}


def client_for(server: ToyServer) -> httpx.AsyncClient:
    """ASGI 直连客户端 (不开端口, 不起服务)."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://toy"
    )


async def ask(server: ToyServer, message: str, **kwargs: Any) -> httpx.Response:
    """POST /runs (响应体里是整条事件流; 挂起时它停在 approval_required)."""
    async with client_for(server) as client:
        return await client.post(
            "/runs", json={"message": message}, headers=headers(**kwargs)
        )


async def resume(
    server: ToyServer, run_id: str, body: dict, **kwargs: Any
) -> httpx.Response:
    """POST /runs/{id}/resume (body 就是 decision + 可选 data)."""
    async with client_for(server) as client:
        return await client.post(
            f"/runs/{run_id}/resume", json=body, headers=headers(**kwargs)
        )


async def cancel(server: ToyServer, run_id: str, **kwargs: Any) -> httpx.Response:
    """POST /runs/{id}/cancel."""
    async with client_for(server) as client:
        return await client.post(f"/runs/{run_id}/cancel", headers=headers(**kwargs))


async def history(server: ToyServer, **kwargs: Any) -> dict:
    """GET /history (刷新页面时前端拿到的那些)."""
    async with client_for(server) as client:
        response = await client.get("/history", headers=headers(**kwargs))
    assert response.status_code == 200, response.text
    return response.json()


async def wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    """等一个条件成立 (轮询 + 超时) —— 与 test_server_app.py 同一个做法."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "等待超时: 条件一直没成立"
        await asyncio.sleep(0.005)


async def wait_for_the_decision(server: ToyServer) -> None:
    """等「谁批的」那一对列写下去.

    它写在任务的**收尾回调**里 (那次运行真的结完之后), 而不是响应回来的那一刻 ——
    客户端读完事件流时它可能还差一步, 于是这里等它一下 (等的是库里那两列非空).
    """

    def written() -> bool:
        return all(
            row["approved_at"] is not None
            for row in server.database.rows_of("charagent_tool_calls")
        )

    await wait_until(written)


def parse_sse(body: str) -> list[dict[str, Any]]:
    """SSE 正文 → 逐事件 (与 test_server_app.py 同一个读法)."""
    events: list[dict[str, Any]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append(
            {
                "id": int(lines["id"]),
                "event": lines["event"],
                "data": json.loads(lines["data"]),
            }
        )
    return events


def suspended_model(order_no: str = "A1", *, answer: str = "已经付好了") -> MockLLM:
    """一个「开口就要付钱」的模型 (挂起那一步的起点).

    两轮脚本: 第一轮要调支付 (于是挂起), 第二轮是**恢复那一段**问它「付完了怎么
    答」—— 一个会话的模型是同一个对象, 两段共用它 (与真实装配同形).
    """
    return MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("pay_order", f'{{"order_no": "{order_no}"}}')
            ),
            text_response(answer),
        ]
    )


def approval_key_of(row: dict) -> IdempotencyKey:
    """一条挂起行 → 它那把幂等键.

    键的拼法**只有一处实现** (`server/app.py` 的 `_approval_key`, 私有). 这里按那个
    拼法复写一份, 是**故意钉住**它: 格式一变, 这两条「重复提交被挡住」的用例当场红
    (它们要造的那把键就对不上了), 于是不会出现「用例还在绿, 生产早换了键」.
    """
    return IdempotencyKey(
        f"resume:{row['run_id']}:{row['message_id']}:{row['tool_call_id']}"
    )


async def suspend_once(server: ToyServer, message: str = "帮我付了这一单") -> str:
    """跑到挂起, 返回那次运行的编号 (从 /history 那块确认卡里读 —— 前端就是这么拿的)."""
    response = await ask(server, message)
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert events[-1]["event"] == "approval_required", (
        f"挂起该以 approval_required 收尾, 实际: {events[-1]['event']}"
    )
    block = (await history(server))[PENDING_APPROVAL_FIELD]
    assert block is not None, "挂起之后 /history 该带着那块确认卡的信息"
    return block[APPROVAL_RUN_ID_FIELD]


# ---------------------------------------------------------------------------
# 挂起那一刻: 事件流 + 刷新之后还在的那张卡
# ---------------------------------------------------------------------------


async def test_a_suspension_ends_the_stream_and_leaves_a_card_behind() -> None:
    """一次要人批的调用: 流停在 approval_required, 而刷新页面拿得到重建卡的四样.

    「四样」= 哪一条(编号) / 哪个工具 / 问什么 / 缺什么 —— 少一样前端就重建不出
    那张卡, 而卡没了用户就永远完不成这次代付 (issue 36 的刷新恢复靠的就是它).
    """
    CALLS.clear()
    server = serve(suspended_model())

    run_id = await suspend_once(server)

    assert CALLS == [], "挂起时那条调用一次都没跑"
    block = (await history(server))[PENDING_APPROVAL_FIELD]
    assert block == {
        APPROVAL_RUN_ID_FIELD: run_id,
        APPROVAL_TOOL_CALL_ID_FIELD: "call_pay_order",
        APPROVAL_TOOL_NAME_FIELD: "pay_order",
        APPROVAL_PROMPT_FIELD: PROMPT,
        APPROVAL_NEEDS_FIELD: ["payment_password"],
    }
    [call] = server.database.rows_of("charagent_tool_calls")
    assert call["status"] == ToolCallStatus.NEEDS_APPROVAL.value
    assert call["approval_prompt"] == PROMPT, "话术落在那一行上 (刷新靠它重建)"
    assert call["approval_needs"] == ["payment_password"]
    assert call["approved_at"] is None, "还没人批过"
    [run] = server.database.rows_of("charagent_runs")
    assert run["status"] == RunStatus.WAITING_USER.value
    assert run["finished_at"] is None, "那一次运行还开着"


# ---------------------------------------------------------------------------
# 恢复: 批准 / 拒绝
# ---------------------------------------------------------------------------


async def test_approving_runs_the_call_and_clears_the_card() -> None:
    """批准: 那条调用真的执行, 未决挂起消失 (再刷新就没有卡了).

    顺带钉住「一次性载荷走的是**重新装配**那次会话」: 恢复请求带来的密码出现在
    工具的闭包里 —— 复用上一段那个会话的话, 它只会在上一段的闭包里打转.
    """
    CALLS.clear()
    server = serve(suspended_model())
    run_id = await suspend_once(server)

    response = await resume(
        server,
        run_id,
        {"decision": "approve", "data": {"payment_password": "pw-123"}},
    )

    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert events[-1]["event"] == "final", f"恢复之后跑完了, 实际: {events[-1]}"
    assert CALLS == ["pay:A1:pw-123"], "那条调用真的执行了, 而且看得见这次的载荷"
    assert (await history(server))[PENDING_APPROVAL_FIELD] is None, "卡该消失了"
    await wait_for_the_decision(server)
    [call] = server.database.rows_of("charagent_tool_calls")
    assert call["status"] == ToolCallStatus.SUCCEEDED.value
    assert call["approved_at"] is not None, "谁批的 / 什么时候批的记在同一行上"
    assert call["approved_by"] == "alice"
    [run] = server.database.rows_of("charagent_runs")
    assert run["status"] == RunStatus.FINISHED.value, "同一次运行的第二段收了尾"


async def test_rejecting_feeds_the_reason_back_and_the_model_continues() -> None:
    """拒绝: 那条调用不执行, 原因当工具结果回填, 模型据此继续答 (不是终止)."""
    CALLS.clear()
    server = serve(suspended_model())
    run_id = await suspend_once(server)

    response = await resume(
        server, run_id, {"decision": "reject", "data": {"reason": "用户取消了付款"}}
    )

    assert response.status_code == 200, response.text
    assert parse_sse(response.text)[-1]["event"] == "final"
    assert CALLS == [], "拒绝就是不执行"
    assert (await history(server))[PENDING_APPROVAL_FIELD] is None
    [call] = server.database.rows_of("charagent_tool_calls")
    assert call["status"] == ToolCallStatus.FAILED.value
    assert call["result"] == "用户取消了付款", "拒绝原因回填给模型 (模型据此继续)"


async def test_an_unknown_decision_is_refused_before_anything_happens() -> None:
    """`decision` 只认 approve / reject: 别的值 400, 那一次调用一动不动."""
    CALLS.clear()
    server = serve(suspended_model())
    run_id = await suspend_once(server)

    response = await resume(server, run_id, {"decision": "yes"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_decision"
    assert CALLS == []
    assert (await history(server))[PENDING_APPROVAL_FIELD] is not None, "还挂着"


# ---------------------------------------------------------------------------
# 闸门: 重复提交 / 没有挂起 / 未决期间的新提问与取消
# ---------------------------------------------------------------------------


async def test_a_second_resume_of_the_same_approval_is_refused() -> None:
    """重复提交被幂等键挡住 (在办的那种) —— 双击的第二下不会变成第二次付款.

    造法: 先把那把键**占住** (等于「第一下正在跑」), 再打一次恢复 —— 于是这一条
    正好验的是那把键本身, 不必去赌两次请求的真实时序.
    """
    CALLS.clear()
    server = serve(suspended_model())
    run_id = await suspend_once(server)
    [call] = server.database.rows_of("charagent_tool_calls")
    key = approval_key_of(call)
    await server.guard.claim(key)

    response = await resume(server, run_id, {"decision": "approve"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_in_progress"
    assert CALLS == [], "被挡下的那次一次都没跑"


async def test_a_resume_after_the_approval_was_applied_is_refused() -> None:
    """已经办完的那一种: 409 (码不同 —— 前端该提示「刷新看最新状态」)."""
    server = serve(suspended_model())
    run_id = await suspend_once(server)
    [call] = server.database.rows_of("charagent_tool_calls")
    key = approval_key_of(call)
    await server.guard.claim(key)
    await server.guard.complete(key, {"applied": True})

    response = await resume(server, run_id, {"decision": "approve"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_already_applied"


async def test_resuming_something_that_is_not_pending_is_not_found() -> None:
    """没有未决挂起 → 404 (跑完了 / 取消过 / 没这个编号 / 不是这段会话都不区分)."""
    server = serve(MockLLM.fixed(text_response("答完了")))
    await ask(server, "随便问一句")

    response = await resume(server, "run-never-existed", {"decision": "approve"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


async def test_a_new_question_while_suspended_is_refused_but_recovery_passes() -> None:
    """未决期间: 新提问 409 (`thread_suspended`), 而 resume / cancel 放行.

    两条都要: 闸门拦住新提问是为了不让新的一句问话与那次未决的调用撞在同一份存档
    上; 而放行另外两条是因为它们**正是**解决这次挂起的动作 —— 拦住它们等于把用户
    锁在门外 (他既不能确认, 也不能取消).
    """
    CALLS.clear()
    server = serve(suspended_model())
    run_id = await suspend_once(server)

    refused = await ask(server, "那算了, 换个问题")

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "thread_suspended", (
        "码要与「会话忙」分开: 前端得能区分「在跑」与「等人」"
    )

    # resume 放行: 闸门看的是「这是不是在解决挂起」, 而不是「这段会话挂着没」
    approved = await resume(server, run_id, {"decision": "approve"})
    assert approved.status_code == 200
    assert CALLS == ["pay:A1:(没有)"], "没带载荷就照常执行 (载荷是可选的)"

    # 而且挂起解决之后, 新提问又能提了
    later = await ask(server, "再问一句")
    assert later.status_code == 200


async def test_cancelling_a_suspension_clears_the_gate() -> None:
    """取消一次挂起: 那次运行收成 cancelled, 那条调用也收掉, 闸门跟着放开.

    挂起的运行**没有任务可取消** (它那次 HTTP 请求早就结束了) —— 要收的是库里那
    两行. 用户由此有一条明路: 不想批就取消, 然后接着聊.
    """
    CALLS.clear()
    server = serve(suspended_model())
    run_id = await suspend_once(server)

    response = await cancel(server, run_id)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    [call] = server.database.rows_of("charagent_tool_calls")
    assert call["status"] == ToolCallStatus.CANCELLED.value
    assert call["approved_by"] == "alice", "谁收的也记在同一行上"
    [run] = server.database.rows_of("charagent_runs")
    assert run["status"] == RunStatus.CANCELLED.value
    assert run["finished_at"] is not None, "取消是终态 (运行到此为止)"
    assert (await history(server))[PENDING_APPROVAL_FIELD] is None

    later = await ask(server, "那再问一句")
    assert later.status_code == 200, "闸门放开了: 新提问又能提了"
    # 而**发给模型的那份历史必须是配对的**: 取消之后会话内存里那份历史停在「欠着
    # 那条调用的结果」的半路上, 直接发出去会被上游拒掉 (真机上是 400: assistant 带
    # tool_calls 后面必须跟上 tool 消息). 修法是取消时把这段会话从登记表里丢掉 ——
    # 下一次提问重新装配并从快照水合, 那时欠着的那条会被补一条「结果未知」的回填
    sent = server.sessions.model.calls[-1]["messages"]
    dangling = [
        message["tool_calls"][0]["id"] for message in sent if message.get("tool_calls")
    ]
    paired = {
        message["tool_call_id"] for message in sent if message.get("role") == "tool"
    }
    assert set(dangling) <= paired, f"欠着结果的调用没被回填: {dangling} vs {paired}"


# ---------------------------------------------------------------------------
# 装配: 没配库就没有这条路
# ---------------------------------------------------------------------------


async def test_the_resume_route_does_not_exist_without_a_database() -> None:
    """没给库 = 挂起态没有家 (ADR-0014), 于是这条路由**不注册**.

    与 `GET /conversations` 同一条规矩: 与其给一个骗人的答案 (「没有挂起」), 不如
    这条路由压根不存在.
    """
    server = ToyServer(MockLLM.fixed(text_response("答完了")), database=None)

    response = await resume(server, "run-1", {"decision": "approve"})

    assert response.status_code == 404
    assert (await history(server))[PENDING_APPROVAL_FIELD] is None
    assert MESSAGES_FIELD in await history(server)


# ---------------------------------------------------------------------------
# 并发的那一下: 真打两次 (第一次在跑时, 第二次被挡)
# ---------------------------------------------------------------------------


async def test_two_resumes_at_once_run_the_call_only_once() -> None:
    """真并发: 第一次还在跑时打第二次 → 409 (`approval_in_progress`), 调用只跑一次.

    这一条走的是**真实的双击窗口** (第一次那条调用还在执行、库里还挂着), 而不是
    「造一个已认领的键」: 两次请求都读到「还挂着」, 而状态那一列还没变 —— 幂等键
    存在的理由正是这个窗口.

    造法是让**工具本身**慢下来 (闸门), 而不是让模型慢: 恢复那一段先补做欠着的调用,
    补做一开始, 那条调用行就不再是「能执行」的了 —— 慢在别处都撞不上这个窗口.
    """
    CALLS.clear()
    gate = asyncio.Event()

    def gated_tools(context: RunContext) -> Sequence[Tool]:
        @tool(annotations={"high_risk": True})
        async def pay_order(order_no: str) -> str:
            """支付一笔订单.

            Args:
                order_no: 订单号.
            """
            await gate.wait()
            CALLS.append(f"pay:{order_no}")
            return f"订单 {order_no} 支付成功"

        return (pay_order,)

    server = serve(suspended_model(answer="付好了"), tools_for=gated_tools)
    run_id = await suspend_once(server)

    first = asyncio.create_task(resume(server, run_id, {"decision": "approve"}))
    try:
        # 等到那次运行真的登记在册 (说明键已经认领、会话已经占住) 再打第二下
        await wait_until(lambda: bool(server.app.state.run_registry.run_ids))
        second = await resume(server, run_id, {"decision": "approve"})

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "approval_in_progress"
    finally:
        # 断言失败也要把闸门打开: 否则第一次那条请求会永远吊在工具调用上
        gate.set()

    assert (await first).status_code == 200
    assert CALLS == ["pay:A1"], "只跑了一次 (另一条请求没被放进来)"


async def test_a_resume_blocked_by_a_busy_session_does_not_poison_the_key() -> None:
    """认领之后才失败的 (会话正忙) 必须把键**放回去** —— 否则这次挂起就被永久锁死.

    这一条盯着「认领在前、占会话在后」那个顺序的代价: 会话正忙时这次恢复根本没跑,
    而键已经到手了. 不放回去的话, 用户等上一次跑完再点会被 409 挡下, 而库里唯一的
    线索是那一把卡在「在办」的键 —— 一次「点不动, 也没人说为什么」.
    """
    CALLS.clear()
    server = serve(suspended_model())
    run_id = await suspend_once(server)
    # 手工把这段会话占住 (等于「上一次还在答」). 走 `resuming=True` 是因为普通的
    # acquire 会被挂起那道闸门拦下 —— 而这里要造的正是「闸门之外还忙着一件事」
    await server.registry.acquire(
        RunContext(thread_id=THREAD, tenant_id="toy", user_id="alice"),
        resuming=True,
    )

    busy = await resume(server, run_id, {"decision": "approve"})

    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "thread_busy"
    assert CALLS == [], "那一次根本没跑起来"

    # 上一次跑完 (放开) 之后再点: 必须能进 —— 键放回去了
    server.registry.release(THREAD)
    approved = await resume(server, run_id, {"decision": "approve"})

    assert approved.status_code == 200, approved.text
    assert CALLS == ["pay:A1:(没有)"], "这次没带载荷, 工具照常执行"
