"""server 应用层 (ASGI 端到端): 一次问答真的能通过 HTTP 拿到完整事件流.

这一页是 server 包的门面用例 —— 用**框架自己的玩具业务**起一个真的 app, 问一句话,
从响应体里读回事件流. 业务与电商毫无关系 (与 test_agent_provider.py 的通用性用例
同一套做法): 一个纯计算的小业务, 一个有身份的小业务 —— **同一套服务代码**装两个
都能跑通, 「通用」就从形容词变成了断言.

测试手段: httpx 的 ASGI transport 直接打 app (不起服务, 不占端口), 模型走
MockLLM, 快照走内存后端 —— 零网络零外部服务.

被测的几条 (对应 ticket 的验收):

- 客户端收到完整事件序列: seq 连续, 终局事件恰好一个, 每个事件都带 run_id
- 同一 thread_id 复用会话 (第二句看得到第一句), 不同 thread_id 并发互不干扰
- 同一 thread_id 并发提问被**明确拒绝** (409), 不是静默串台
- 认证失败 → 状态码 + 一句不区分细节的话; 业务配置错 → 503 而不是 500 + traceback
- 运行中失败 / 被取消 → 事件流有明确的结尾 (终局 error), 而不是静默断连
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response
from starlette.applications import Starlette

from CharAgent.agent import RunContext
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import ModelMessage, ModelResponse
from CharAgent.server import (
    ServerAuthError,
    ServerConfigError,
    create_app,
)
from CharAgent.tool import Tool, tool

# 两个玩具业务的工具集 (与电商毫无关系的两件小事)
TOKEN = "toy-token"


@tool
async def sum_numbers(left: int, right: int) -> str:
    """求两个整数之和.

    Args:
        left: 第一个加数.
        right: 第二个加数.
    """
    return str(left + right)


@tool
async def reverse_text(text: str) -> str:
    """把一段文本倒过来.

    Args:
        text: 要倒置的文本.
    """
    return text[::-1]


TOY_TOOLS: tuple[Tool, ...] = (sum_numbers, reverse_text)


def badge_tools(context: RunContext) -> Sequence[Tool]:
    """业务 B 的工具: 身份在装配时裹进闭包, 模型从头到尾看不见它 (#4.2)."""

    @tool
    async def my_badge() -> str:
        """查看当前用户自己的工牌号."""
        return f"工牌: {context.payload['user']}"

    return (*TOY_TOOLS, my_badge)


# ---------------------------------------------------------------------------
# 玩具业务的两个插座 (业务侧该长什么样, 这里就是最小样板)
# ---------------------------------------------------------------------------


class ToyContexts:
    """插座一: 认证 + 解析 (两个玩具业务共用同一个实现, 只换参数)."""

    def __init__(self, *, thread_header: str = "X-Thread-Id") -> None:
        self._thread_header = thread_header
        self.seen: list[tuple[str, str]] = []

    async def provide(self, request: httpx.Request) -> RunContext:
        if request.headers.get("X-Toy-Token") != TOKEN:
            # 默认消息 (「认证失败」) 刻意不区分「令牌错」还是「没带令牌」
            raise ServerAuthError()
        thread_id = request.headers.get(self._thread_header, "toy:default")
        user = request.headers.get("X-Toy-User", "anonymous")
        self.seen.append((thread_id, user))
        # 属主那两个字段是框架**认识**的 (会话列表按它们过滤); 玩具业务的
        # 「用户」就填在这里, payload 仍然留给业务私货
        return RunContext(
            thread_id=thread_id,
            tenant_id="toy",
            user_id=user,
            payload={"user": user},
        )


@dataclass
class ToySessions:
    """插座二: 装配 (模型 / 工具 / 提示词都在这里定, 两个业务的差别只有工具)."""

    model: ChatModel
    tools_for: Callable[[RunContext], Sequence[Tool]] = lambda context: TOY_TOOLS
    prompt_name: str = "system"
    fail_with: Exception | None = None
    sessions: list[ChatSession] = field(default_factory=list)

    async def provide(self, context: RunContext, *, event_sink) -> ChatSession:
        if self.fail_with is not None:
            raise self.fail_with
        session = ChatSession(
            self.model,
            saver=InMemoryCheckpointSaver(),
            tools=list(self.tools_for(context)),
            thread_id=context.thread_id,
            event_sink=event_sink,
            prompt_name=self.prompt_name,
        )
        self.sessions.append(session)
        return session


@dataclass
class ToyServer:
    """一整套玩具服务: app + 两个插座 (测试里拿它断言与出请求)."""

    app: Starlette
    contexts: ToyContexts
    sessions: ToySessions

    @property
    def session_registry(self):
        return self.app.state.session_registry

    @property
    def run_registry(self):
        return self.app.state.run_registry


def serve(*, model: ChatModel, **kwargs: Any) -> ToyServer:
    """起一个玩具服务 (同一行 create_app 服务两个业务, 差别只在参数)."""
    contexts = ToyContexts()
    sessions = ToySessions(model, **kwargs)
    return ToyServer(
        app=create_app(context_provider=contexts, session_provider=sessions),
        contexts=contexts,
        sessions=sessions,
    )


def client_for(server: ToyServer) -> httpx.AsyncClient:
    """ASGI 直连客户端 (不开端口, 不起服务)."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://toy"
    )


def headers(
    *, thread: str = "toy:chat-1", user: str = "alice", token: str = TOKEN
) -> dict:
    """业务自己那套头 (框架一个都不认识: 认证与身份都是业务的事).

    值一律 ASCII: HTTP 头的字节是 latin-1 (httpx 里更是直接按 ASCII 编), 中文
    身份塞进头里会在客户端就炸掉 —— 真实业务该把这类值放进请求体 (UTF-8).
    """
    return {"X-Toy-Token": token, "X-Toy-User": user, "X-Thread-Id": thread}


async def ask(
    server: ToyServer,
    message: str,
    *,
    thread: str = "toy:chat-1",
    user: str = "alice",
    token: str = TOKEN,
) -> httpx.Response:
    """打一次 POST /runs 并等这一次运行跑完 (响应体里就是整条事件流)."""
    async with client_for(server) as client:
        return await client.post(
            "/runs",
            json={"message": message},
            headers=headers(thread=thread, user=user, token=token),
        )


async def cancel(
    server: ToyServer,
    run_id: str,
    *,
    thread: str = "toy:chat-1",
    user: str = "alice",
    token: str = TOKEN,
) -> httpx.Response:
    """打一次 POST /runs/{id}/cancel (默认是那个能取消得动的身份)."""
    async with client_for(server) as client:
        return await client.post(
            f"/runs/{run_id}/cancel",
            headers=headers(thread=thread, user=user, token=token),
        )


# ---------------------------------------------------------------------------
# 事件流的读法 (SSE 帧 → 事件字典)
# ---------------------------------------------------------------------------


def parse_sse(body: str) -> list[dict[str, Any]]:
    """SSE 正文 → 逐事件: {"id": 序号, "event": 类型, "data": 载荷}.

    只解析本层产出的形状 (三行一帧 + 空行分隔), 不做通用 SSE 解析.
    """
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


def terminal_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """终局事件 (final / error) —— 一条流里恰好一个."""
    return [event for event in events if event["event"] in ("final", "error")]


async def wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    """等一个条件成立 (轮询 + 超时); 「等到某件事发生」在事件循环里没有现成原语."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "等待超时: 条件一直没成立"
        await asyncio.sleep(0)


def gated(gate: asyncio.Event, response: ModelResponse) -> Callable:
    """一段「等到闸门开了再回答」的脚本步骤 (让运行停在半路, 便于并发用例)."""

    async def step(messages: list[ModelMessage]) -> ModelResponse:
        await gate.wait()
        return response

    return step


# ---------------------------------------------------------------------------
# 一次问答走通 (验收第一条)
# ---------------------------------------------------------------------------


async def test_a_question_comes_back_as_a_complete_event_stream() -> None:
    """问一句, 拿回完整事件序列: seq 连续, 终局事件恰好一个, 每个事件带 run_id.

    这一条把「框架的流式事件」与「HTTP 的 SSE」两件事接在一起验: 工具调用与
    结果都在, 顺序不乱, 编号从 1 起没有断号 (客户端的连续性全靠它).
    """
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("sum_numbers", '{"left": 2, "right": 3}')
            ),
            text_response("2 加 3 等于 5"),
        ]
    )
    server = serve(model=model)

    response = await ask(server, "2 加 3 是多少")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    run_id = response.headers["x-run-id"]

    events = parse_sse(response.text)
    assert [event["event"] for event in events] == [
        "tool_call",
        "tool_result",
        "final",
    ], "工具调用与结果都推给了客户端, 最后是答复"
    assert [event["id"] for event in events] == [1, 2, 3], "seq 从 1 起连续"
    assert all(event["event"] == event["data"]["type"] for event in events)
    assert all(event["data"]["run_id"] == run_id for event in events)

    call, result, final = events
    assert call["data"]["tool_name"] == "sum_numbers"
    assert result["data"]["status"] == "ok"
    assert result["data"]["summary"] == "5", "工具的结果真的回填给了模型与前端"
    assert final["data"]["content"] == "2 加 3 等于 5"


async def test_the_run_and_the_session_are_released_when_it_ends() -> None:
    """一次问答跑完之后: 运行出册, 会话不再占着 (下一句还能问)."""
    server = serve(model=MockLLM.fixed(text_response("好")))

    await ask(server, "在吗")

    assert server.run_registry.run_ids == (), "跑完就该出册 (否则取消端点会摸到幽灵)"
    assert server.session_registry.busy_threads == frozenset()
    assert server.session_registry.thread_ids == ("toy:chat-1",), "会话留着复用"


# ---------------------------------------------------------------------------
# 通用性: 同一套服务代码, 两个毫不相干的业务 (验收第二条)
# ---------------------------------------------------------------------------


async def test_the_same_server_serves_two_unrelated_businesses() -> None:
    """同一套服务代码装两个业务都能跑通 —— 「通用」从形容词变成断言.

    业务 A 是纯计算 (不需要身份), 业务 B 的工具要身份 (从载荷裹进闭包). 两者的
    差别只在**参数**: 同一个 create_app, 同一套插座实现, 同一个 ask().
    """
    arithmetic = serve(
        model=MockLLM.scripted(
            [
                tool_call_response(make_tool_call("reverse_text", '{"text": "abc"}')),
                text_response("倒过来是 cba"),
            ]
        )
    )
    identity = serve(
        model=MockLLM.scripted(
            [
                tool_call_response(make_tool_call("my_badge")),
                text_response("你的工牌是 alice"),
            ]
        ),
        tools_for=badge_tools,
    )

    arithmetic_response = await ask(arithmetic, "把 abc 倒过来", thread="toy:a:1")
    identity_response = await ask(identity, "我的工牌号是多少", thread="toy:b:1")

    assert "倒过来是 cba" in arithmetic_response.text
    assert "工牌: alice" in identity_response.text, "身份真的到达了业务 B 的工具"
    assert "工牌" not in arithmetic_response.text, "业务 A 不该看见业务 B 的东西"


# ---------------------------------------------------------------------------
# 会话: 同一 thread 连问两句 / 两个 thread 并发
# ---------------------------------------------------------------------------


async def test_the_second_question_sees_the_first_one() -> None:
    """同一 thread_id 连问两句: 第二句的模型请求里带着第一句的问答.

    这是「会话按 thread_id 长驻」这条设计的**目的**: 会话对象里的历史活着,
    多轮对话在 HTTP 上才成立 (每个请求新建会话的第二轮就失忆了).
    """
    model = MockLLM.fixed(text_response("好的"))
    server = serve(model=model)

    await ask(server, "第一句")
    await ask(server, "第二句")

    assert len(server.sessions.sessions) == 1, "两句话只该建一个会话"
    second_call = model.calls[1]["messages"]
    contents = [message["content"] for message in second_call]
    assert "第一句" in contents, "第二句的请求里要带第一句"
    assert "好的" in contents, "也要带上第一句的答复"
    assert contents[-1] == "第二句"


async def badge_dialogue(messages: list[ModelMessage]) -> ModelResponse:
    """「先查工牌, 再作答」的脚本: 按**本轮历史**决定这一步干什么.

    为什么要看历史而不是排队好的脚本: 这一段要被两个并发的会话共用 (同一个模型
    对象), 而顺序弹出的脚本会被两个请求交错消耗, 谁拿到哪一步全看运气.
    """
    if messages[-1]["role"] == "tool":
        return text_response("已查到")
    return tool_call_response(make_tool_call("my_badge"))


async def test_two_threads_run_at_once_without_mixing() -> None:
    """两个会话并发跑各拿各的 (会话按 thread_id 分区, 互不干扰)."""
    server = serve(model=MockLLM.fixed(badge_dialogue), tools_for=badge_tools)

    responses = await asyncio.gather(
        ask(server, "我的工牌号是多少", thread="toy:alice", user="alice"),
        ask(server, "我的工牌号是多少", thread="toy:bob", user="bob"),
    )

    first, second = (parse_sse(response.text) for response in responses)
    assert "工牌: alice" in json.dumps(first, ensure_ascii=False)
    assert "工牌: bob" not in json.dumps(first, ensure_ascii=False), "别串到别人的身份"
    assert "工牌: bob" in json.dumps(second, ensure_ascii=False)
    assert len(server.sessions.sessions) == 2


async def test_a_second_question_on_a_busy_thread_is_refused() -> None:
    """同一 thread_id 上还有一句在答时, 第二句被明确拒绝 (409), 不是静默串台.

    拒绝而不是排队 (框架不替业务发明队列策略), 但拒绝要**可解释**: 状态码 +
    机器读的错误码, 前端据此提示「上一句还在答」. 第一句不受影响, 正常答完.
    """
    gate = asyncio.Event()
    model = MockLLM.fixed(gated(gate, text_response("好的")))
    server = serve(model=model)

    first = asyncio.create_task(ask(server, "第一句"))
    await wait_until(lambda: bool(server.run_registry.run_ids))

    second = await ask(server, "第二句")

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "thread_busy"
    assert server.session_registry.busy_threads == frozenset({"toy:chat-1"}), (
        "被拒的那句不该把会话放开"
    )

    gate.set()
    assert (await first).status_code == 200
    assert server.session_registry.busy_threads == frozenset(), "第一句跑完就放开了"


async def test_a_new_question_is_accepted_after_the_busy_one_finishes() -> None:
    """忙完就放开: 被拒过一次的会话, 下一句照样能问 (占位不能留在那儿)."""
    gate = asyncio.Event()
    model = MockLLM.fixed(gated(gate, text_response("好的")))
    server = serve(model=model)

    first = asyncio.create_task(ask(server, "第一句"))
    await wait_until(lambda: bool(server.run_registry.run_ids))
    assert (await ask(server, "第二句")).status_code == 409

    gate.set()
    await first

    assert (await ask(server, "第三句")).status_code == 200


# ---------------------------------------------------------------------------
# 出错的时候: 认证 / 配置 / 请求体
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["wrong-token", ""], ids=["令牌不对", "没带令牌"])
async def test_a_failed_authentication_is_one_plain_answer(token: str) -> None:
    """认证失败: 明确的状态码 + 一句不区分细节的话 (别泄漏是哪儿不对).

    两种情况必须**长得一模一样** —— 否则「令牌错」与「没带令牌」的差别就是一条
    免费的情报 (攻击者据此判断令牌猜得对不对). 框架的默认消息已经这么写了,
    业务要做的就是别把细节塞进去.
    """
    server = serve(model=MockLLM.fixed(text_response("好的")))

    response = await ask(server, "在吗", token=token)

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "unauthorized", "message": "认证失败"}}
    assert "Traceback" not in response.text
    assert server.contexts.seen == [], "没认下来的请求不该走到解析之后"


async def test_the_business_config_error_becomes_a_clean_answer() -> None:
    """业务装配时发现配置不对 → 503 + 一句事实, 不是 500 + traceback.

    配置类错误与 bug 要分得开: 前者是「现在跑不了」(客户端可以重试, 运维该去查
    配置), 后者是「代码有问题」(该留 traceback 给自己看).
    """
    server = serve(
        model=MockLLM.fixed(text_response("好的")),
        fail_with=ServerConfigError("模型 Key 没配"),
    )

    response = await ask(server, "在吗")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"
    assert response.json()["error"]["message"] == "模型 Key 没配"
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    ("body", "note"),
    [
        ("not json at all", "不是 JSON"),
        ('{"say": "你好"}', "缺 message 字段"),
        ('{"message": "   "}', "message 是空白"),
        ('{"message": 42}', "message 不是字符串"),
    ],
)
async def test_a_request_without_a_message_is_refused(body: str, note: str) -> None:
    """请求体不成形 → 400 + 机器读的错误码 (框架自己的 HTTP 契约)."""
    server = serve(model=MockLLM.fixed(text_response("好的")))

    async with client_for(server) as client:
        response = await client.post(
            "/runs",
            content=body.encode("utf-8"),
            headers={**headers(), "content-type": "application/json"},
        )

    assert response.status_code == 400, note
    assert response.json()["error"]["code"] == "invalid_request"
    assert server.sessions.sessions == [], "读不懂的请求不该顺手建会话"


# ---------------------------------------------------------------------------
# 运行中途出错 / 被取消: 事件流要有干净的结尾 (验收最后两条)
# ---------------------------------------------------------------------------


async def test_a_run_failure_ends_the_stream_with_one_terminal_error() -> None:
    """跑到一半挂了: 事件流补一个终局 error 收尾, 不是静默断连.

    这条走的是**响应已经开始**之后的失败 —— 那时不可能再回一个状态码, 只能靠
    终局事件把「没有答复」这件事如实告诉客户端 (前端据此收尾, 而不是一直转圈).
    """

    async def boom(messages: list[ModelMessage]) -> ModelResponse:
        raise RuntimeError("上游炸了")

    server = serve(model=MockLLM.scripted([boom]))

    response = await ask(server, "在吗")

    assert response.status_code == 200, "流已经开出去了, 状态码不会再变"
    events = parse_sse(response.text)
    assert [event["event"] for event in terminal_events(events)] == ["error"]
    assert events[-1]["data"]["error"]["code"] == "run_failed"
    assert "上游炸了" in events[-1]["data"]["error"]["message"]


async def test_a_cancelled_run_ends_with_cancelled() -> None:
    """取消一次运行: 事件流以 error(code=cancelled) 收尾, 且恰好一个终局事件.

    触发走的是**真的那一条路** (POST /runs/{id}/cancel), 不是直接摸任务句柄 ——
    客户端看到的东西由边界决定, 而边界就是那个端点.
    """
    gate = asyncio.Event()  # 一直不开: 这次运行会一直等模型
    server = serve(model=MockLLM.fixed(gated(gate, text_response("好的"))))

    pending = asyncio.create_task(ask(server, "在吗"))
    await wait_until(lambda: bool(server.run_registry.run_ids))
    [run_id] = server.run_registry.run_ids

    assert (await cancel(server, run_id)).status_code == 200
    response = await pending

    events = parse_sse(response.text)
    assert [event["event"] for event in terminal_events(events)] == ["error"]
    assert events[-1]["data"]["error"]["code"] == "cancelled"
    assert server.run_registry.run_ids == (), "取消之后也要出册"
    assert server.session_registry.busy_threads == frozenset(), (
        "取消之后会话要放开 (不然「继续」这句会被 409 拒掉)"
    )


# ---------------------------------------------------------------------------
# 取消端点 (07): 谁按得动它, 以及按不动的时候回什么
# ---------------------------------------------------------------------------


async def test_a_cancel_request_stops_a_running_run_promptly() -> None:
    """按一下停止: 运行真的停下, 而且**立刻就停** (不是等这一轮跑完).

    即时性对齐 `test_loop_guard.py` 那条 kill switch 用例的断言方式 (那里是
    「工具在睡 30 秒」, 这里是「模型一直不回」)—— 两处都是「等它自然结束要很久,
    而取消必须远快于那个很久」. 2 秒是宽裕的调度余量, 真机上是毫秒级.
    """
    gate = asyncio.Event()
    server = serve(model=MockLLM.fixed(gated(gate, text_response("好的"))))

    pending = asyncio.create_task(ask(server, "在吗"))
    await wait_until(lambda: bool(server.run_registry.run_ids))
    [run_id] = server.run_registry.run_ids

    started = time.perf_counter()
    response = await cancel(server, run_id)
    streamed = await pending

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
    assert time.perf_counter() - started < 2
    assert streamed.status_code == 200, "流早就开出去了, 取消不改状态码"
    assert parse_sse(streamed.text)[-1]["data"]["error"]["code"] == "cancelled"


async def test_cancelling_a_run_that_already_ended_is_not_a_crash() -> None:
    """取消一个已经跑完的运行: 明确的 404 + 一个机器读的码, 不是 500.

    正常跑完的运行**已经出册了** (出册挂在任务的收尾回调上, 见 runs.py), 所以本层
    手上确实什么都没有 —— 这时能给的只有「这次运行不在了」这一句事实.
    """
    server = serve(model=MockLLM.fixed(text_response("好的")))
    finished = await ask(server, "在吗")

    response = await cancel(server, finished.headers["x-run-id"])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"
    assert "Traceback" not in response.text


async def test_an_unknown_run_answers_exactly_like_a_finished_one() -> None:
    """不存在的运行与已结束的运行**给同一个回答** (不区分是刻意的).

    两条理由: 一是本层真的分不出来 (登记表只记在跑的), 二是分出来也没好处 ——
    「这个 run_id 存在过」本身就是一条情报, 而没有哪个正常客户端需要它. 于是两种情况
    从状态码到响应体逐字节相同, 想区分的是**日志**.
    """
    server = serve(model=MockLLM.fixed(text_response("好的")))
    finished = await ask(server, "在吗")

    over = await cancel(server, finished.headers["x-run-id"])
    unknown = await cancel(server, "0" * 32)

    assert over.status_code == unknown.status_code == 404
    assert over.json()["error"]["code"] == unknown.json()["error"]["code"]
    # 说明文字里唯一的差别是**回显的那个编号** (调用方自己给的, 不是新情报)
    assert over.json()["error"]["message"].replace(
        finished.headers["x-run-id"], "<run>"
    ) == unknown.json()["error"]["message"].replace("0" * 32, "<run>")


async def test_a_run_belonging_to_another_conversation_cannot_be_cancelled() -> None:
    """别人的运行取消不了 —— **判据是身份, 不是参数**.

    运行属于哪段会话是登记时记下的 (`RunHandle.thread_id`), 而「你是谁」由业务那
    个插座认出来; 两者对不上就当它不存在. 这里还断言被拒之后**对方的运行照跑**
    (拒绝不是「取消了但没告诉你」).
    """
    gate = asyncio.Event()
    server = serve(model=MockLLM.fixed(gated(gate, text_response("好的"))))

    alice = asyncio.create_task(ask(server, "在吗", thread="toy:alice", user="alice"))
    await wait_until(lambda: bool(server.run_registry.run_ids))
    [run_id] = server.run_registry.run_ids

    refused = await cancel(server, run_id, thread="toy:bob", user="bob")

    assert refused.status_code == 404
    assert refused.json()["error"]["code"] == "run_not_found"
    assert server.run_registry.run_ids == (run_id,), "别人的运行还在跑"

    gate.set()
    assert (await alice).status_code == 200


async def test_the_cancel_endpoint_asks_the_business_who_is_calling() -> None:
    """认证不过: 401, 而且**一次都不碰**运行 (不认识的人不该有任何影响力).

    取消端点复用的是 `/runs` 那道门 (同一个 ContextProvider) —— 这不是省事, 是
    同一片资源本来就该有同一道门禁; 于是「令牌错」在这里的表现与那里逐字相同.
    """
    gate = asyncio.Event()
    server = serve(model=MockLLM.fixed(gated(gate, text_response("好的"))))

    pending = asyncio.create_task(ask(server, "在吗"))
    await wait_until(lambda: bool(server.run_registry.run_ids))
    [run_id] = server.run_registry.run_ids

    response = await cancel(server, run_id, token="wrong-token")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert server.run_registry.run_ids == (run_id,), "没认下来的请求不该动到运行"

    gate.set()
    await pending


async def test_cancelling_one_run_leaves_the_others_alone() -> None:
    """取消的是**那一次**运行, 不是这个进程里的运行 (别人的照跑)."""
    gate = asyncio.Event()
    server = serve(model=MockLLM.fixed(gated(gate, text_response("好的"))))

    alice = asyncio.create_task(ask(server, "在吗", thread="toy:alice", user="alice"))
    bob = asyncio.create_task(ask(server, "在吗", thread="toy:bob", user="bob"))
    await wait_until(lambda: len(server.run_registry.run_ids) == 2)
    handles = {
        handle.thread_id: handle
        for handle in (server.run_registry.get(r) for r in server.run_registry.run_ids)
    }

    assert (
        await cancel(server, handles["toy:alice"].run_id, thread="toy:alice")
    ).status_code == 200
    gate.set()

    stopped = parse_sse((await alice).text)
    assert stopped[-1]["data"]["error"]["code"] == "cancelled"
    events = parse_sse((await bob).text)
    assert [event["event"] for event in terminal_events(events)] == ["final"]
    assert events[-1]["data"]["content"] == "好的"
    assert server.run_registry.run_ids == ()


async def test_a_question_after_a_cancel_continues_instead_of_redoing() -> None:
    """停下来的那次运行, 已经做完的事留在历史里 —— 接着说一句就接着走.

    这是取消的**下半句**: 停不是把这次对话作废 (那是「重来」), 而是「先别往下查了」.
    框架靠 `ChatSession._reclaim_progress` 把快照里已完成的工作收回历史 (只做加法),
    所以用户说的「继续」就是一次普通提问, 模型自己看着历史接上.

    两条断言各管一半: 「工具没被重跑」证明没有从头再来, 「历史里有 tool 消息」证明
    模型真看得到做过什么 —— 后者才是前者的原因 (光不重跑也可能是模型恰好没调).

    模型按**本轮历史**现算 (拍一段固定脚本的话, 被取消那一步照样会被弹掉, 于是取消
    恰好落在哪一步就成了运气 —— 与 `badge_dialogue` 同一条理由).
    """
    ran: list[str] = []
    gate = asyncio.Event()

    @tool
    async def tally() -> str:
        """在账本上记一笔, 返回账本上一共几笔."""
        ran.append("tally")
        return str(len(ran))

    async def dialogue(messages: list[ModelMessage]) -> ModelResponse:
        if messages[-1]["content"] == "继续":
            return text_response("接着答")
        if not any(message["role"] == "tool" for message in messages):
            return tool_call_response(make_tool_call("tally"))
        await gate.wait()  # 第一句的第二轮: 停在这儿等客户端按停止
        return text_response("不会走到这里")

    model = MockLLM.fixed(dialogue)
    server = serve(model=model, tools_for=lambda context: (tally,))

    pending = asyncio.create_task(ask(server, "记一笔"))
    # 等到第二次模型调用**已经开始**: 那一刻第一轮的快照已经落盘 (工具结果在档里),
    # 取消之后才收得回来 —— 否则这条用例测的是「取消得比落盘慢」, 不是「接着走」
    await wait_until(lambda: len(model.calls) == 2)
    await wait_until(lambda: bool(server.run_registry.run_ids))
    [run_id] = server.run_registry.run_ids

    assert (await cancel(server, run_id)).status_code == 200
    assert parse_sse((await pending).text)[-1]["data"]["error"]["code"] == "cancelled"

    continued = await ask(server, "继续")

    assert continued.status_code == 200
    assert parse_sse(continued.text)[-1]["data"]["content"] == "接着答"
    assert ran == ["tally"], "已完成的那一步不该重做"
    seen = model.calls[-1]["messages"]
    assert any(message["role"] == "tool" for message in seen), (
        "取消前那次工具调用要留在历史里 (否则模型只能从头再查一遍)"
    )
    assert seen[-1]["content"] == "继续"
