"""HTTP 那一路的号牌 (difficulties #38): request_id 从哪来, 又怎么跟进去.

四条, 前三条逐字对应票面验收:

1. `X-Request-Id` 头带了就用它, 并在响应头里回同一个值;
2. 没带 (或带的那个用不了) 就自己发一个 —— 号是**我们**用来关联的东西;
3. **两个并发请求的 request_id 不相同**: 票面原话是「这一条直接钉住『别学
   rag_text2sql 写死』」(那边把 request_id 写成一个常量, 于是所有请求同一个号);
4. **一次运行的日志带着三个号** —— 中间件绑的 `request_id` 要传得进那次运行
   (它跑在一个独立的任务里), 而 `thread_id` / `run_id` 由会话层绑.

第 4 条是这一组的重点: 中间件与下游在不在同一个上下文里, 是「裸 ASGI 还是
`BaseHTTPMiddleware`」那个选择赌的就是它 —— 这里拿真 app 跑一遍把它钉住.

业务侧那份敏感字段名单在这一层的落地在 `CharApp` 的 `build_redactor` 里, 框架这
一层只认协议 —— 所以打码那三条在 `test_logging.py`.

测试手段与 `test_server_app.py` 同款: httpx 的 ASGI transport 直连 app (不起服务,
不占端口), 模型走 MockLLM, 快照走内存后端, 记录层走假库 —— 零网络零外部服务.
"""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.agent import RunContext
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.db import PriceTable
from CharAgent.db.recorder import ConversationRecorder
from CharAgent.db.testing import FakeRecordDatabase
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import ModelMessage, ModelResponse
from CharAgent.server import ServerAuthError, create_app
from CharAgent.server.middleware import RequestIdMiddleware
from CharAgent.server.utils.types import REQUEST_ID_HEADER
from CharAgent.structured_logging import get_logger
from CharAgent.tool import tool

TOKEN = "toy-token"
THREAD = "toy:logging:1"

# 探针工具打日志用的 logger (取短名 —— 工厂会把它挂到 `charagent` 树下)
PROBE_LOGGER = get_logger("tests.http.probe")

# 全天一价那张表 (不用日历): 记录员构造时要一份价目表, 给死免得看环境变量
FLAT_PRICES = PriceTable.from_json(
    '{"models": {"deepseek-flash": {"cache_miss": 2, "cache_hit": 0.5, "output": 8}}}'
)


@tool
async def probe() -> str:
    """探针工具: 打一行日志, 再回一句固定的话.

    为什么要借工具打这一行 (而不是在别处): 运行跑在**中间件之外的一个任务**里
    (框架 `asyncio.create_task(entry.session.ask(message))`), 号能不能传进去正是
    本组要验的; 而工具是那次运行里最深的一层, 它带着号就说明整条链都带着.

    Returns:
        str: 固定的一句话.
    """
    PROBE_LOGGER.info("工具探针在跑")
    return "ok"


async def two_phase(messages: list[ModelMessage]) -> ModelResponse:
    """两拍脚本: 还没有工具结果就要调工具, 有了就回正文.

    写成**按历史判断**而不是按调用次数弹预设值: 并发跑两次运行共用一个模型实例,
    弹序列的次序会随交织方式变 (谁先弹到第二个很随机), 而这一条与历史一一对应,
    怎么交织都对.
    """
    if any(message.get("role") == "tool" for message in messages):
        return text_response("好了")
    return tool_call_response(make_tool_call("probe"))


# ---------------------------------------------------------------------------
# 玩具业务: 一个认证 + 一个装配 (与 test_server_app.py 同形, 只留这一组要的)
# ---------------------------------------------------------------------------


class ToyContexts:
    """插座一: 认证 + 解析."""

    async def provide(self, request: httpx.Request) -> RunContext:
        if request.headers.get("X-Toy-Token") != TOKEN:
            raise ServerAuthError()
        return RunContext(
            thread_id=request.headers.get("X-Thread-Id", THREAD),
            tenant_id="toy",
            user_id="alice",
        )


@dataclass
class ToySessions:
    """插座二: 装配 (配了记录员, 运行编号才有地方来)."""

    model: ChatModel
    sessions: list[ChatSession] = field(default_factory=list)

    async def provide(self, context: RunContext, *, event_sink) -> ChatSession:
        session = ChatSession(
            self.model,
            saver=InMemoryCheckpointSaver(),
            tools=[probe],
            thread_id=context.thread_id,
            model_name="deepseek-flash",
            event_sink=event_sink,
            recorder=ConversationRecorder(
                database=FakeRecordDatabase(),
                tenant_id="toy",
                user_id="alice",
                prices=FLAT_PRICES,
            ),
        )
        self.sessions.append(session)
        return session


@dataclass
class ToyServer:
    """一整套玩具服务: app + 两个插座."""

    app: FastAPI
    contexts: ToyContexts
    sessions: ToySessions


def serve(model: ChatModel) -> ToyServer:
    """起一个玩具服务 (与业务那一行 `create_app` 同一个函数)."""
    contexts = ToyContexts()
    sessions = ToySessions(model)
    app = create_app(context_provider=contexts, session_provider=sessions)
    return ToyServer(app=app, contexts=contexts, sessions=sessions)


def client_for(server: ToyServer) -> httpx.AsyncClient:
    """ASGI 直连客户端 (不开端口, 不起服务)."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://toy"
    )


def headers(
    *, thread: str = THREAD, token: str = TOKEN, request_id: str | None = None
) -> dict[str, str]:
    """业务自己那套头 (框架一个都不认识) + 可选的那个请求编号头."""
    head = {"X-Toy-Token": token, "X-Thread-Id": thread}
    if request_id is not None:
        head[REQUEST_ID_HEADER] = request_id
    return head


def lines_of(stream: io.StringIO) -> list[dict]:
    """缓冲里的一行行 JSON."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def probe_lines(stream: io.StringIO) -> list[dict]:
    """探针工具打的那几行 (只有它们能证明「号跟进了那次运行」)."""
    return [line for line in lines_of(stream) if line["logger"] == PROBE_LOGGER.name]


# ---------------------------------------------------------------------------
# 一、号从哪来
# ---------------------------------------------------------------------------


async def test_the_request_id_comes_from_the_header(log_stream: io.StringIO) -> None:
    """头里带了就用它, 并回同一个值 —— 客户端拿着它才能在日志里指名道姓地找."""
    server = serve(MockLLM.scripted([two_phase, two_phase]))

    async with client_for(server) as client:
        response = await client.post(
            "/runs", json={"message": "跑一下"}, headers=headers(request_id="req-abc")
        )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "req-abc"
    assert {line["request_id"] for line in probe_lines(log_stream)} == {"req-abc"}


async def test_a_missing_request_id_is_generated(log_stream: io.StringIO) -> None:
    """没带就自己发一个 (空着等于这次排查少了根线头)."""
    server = serve(MockLLM.scripted([two_phase, two_phase]))

    async with client_for(server) as client:
        response = await client.post(
            "/runs", json={"message": "跑一下"}, headers=headers()
        )

    generated = response.headers[REQUEST_ID_HEADER]
    assert generated, "响应头里该有一个号"
    assert {line["request_id"] for line in probe_lines(log_stream)} == {generated}


async def test_an_unusable_request_id_is_replaced(log_stream: io.StringIO) -> None:
    """头里那个用不了 (有怪字符) 就换一个 —— 号不是客户端往日志里塞东西的地方."""
    server = serve(MockLLM.scripted([two_phase, two_phase]))

    async with client_for(server) as client:
        response = await client.post(
            "/runs",
            json={"message": "跑一下"},
            headers=headers(request_id="bad;id"),
        )

    assert response.headers[REQUEST_ID_HEADER] != "bad;id"


# ---------------------------------------------------------------------------
# 二、两个并发请求不许是同一个号 (票面点名的那个反面教材)
# ---------------------------------------------------------------------------


async def test_two_concurrent_requests_get_different_request_ids(
    log_stream: io.StringIO,
) -> None:
    """并发两次请求: 两个号不相同, 而且**日志里真的分了家**.

    只断言「两个响应头不同」是不够的 —— 号是在上下文里传的, 而 contextvars 用错
    (拿 `threading.local`) 的表现恰恰是「并发时互相串号」: 响应头各是各的, 日志里
    却混成了同一个. 所以两件事一起断言.
    """
    server = serve(MockLLM.scripted([two_phase] * 4))

    async with client_for(server) as client:
        first, second = await asyncio.gather(
            client.post(
                "/runs", json={"message": "一"}, headers=headers(thread="toy:conc:a")
            ),
            client.post(
                "/runs", json={"message": "二"}, headers=headers(thread="toy:conc:b")
            ),
        )

    id_a = first.headers[REQUEST_ID_HEADER]
    id_b = second.headers[REQUEST_ID_HEADER]

    assert id_a and id_b
    assert id_a != id_b, "两个并发请求拿到了同一个 request_id"

    # 每个号各自都在日志里出现过 —— 一次请求的每一行都只带自己那个号
    seen = {line["request_id"] for line in lines_of(log_stream) if line["request_id"]}
    assert {id_a, id_b} <= seen

    # **按会话分堆**对一遍, 而不是只看「两个号都在日志里」: 那一种在**两个号被对调**
    # 时照样通过 (A 的行带 B 的号, 反过来也一样 —— 集合层面看不出差别). 两次运行
    # 用的会话编号不同, 于是「哪一行的号该是哪一个」是确定的: 响应头与行按 thread
    # 一比, 串号就无处可藏.
    by_thread: dict[str, set[str | None]] = {}
    for line in probe_lines(log_stream):
        by_thread.setdefault(line["thread_id"], set()).add(line["request_id"])

    assert by_thread == {
        "toy:conc:a": {id_a},
        "toy:conc:b": {id_b},
    }, f"号与请求对不上: {by_thread}"


# ---------------------------------------------------------------------------
# 三、三个号在**同一次运行**里齐了
# ---------------------------------------------------------------------------


async def test_a_run_carries_all_three_ids(log_stream: io.StringIO) -> None:
    """一次运行的日志带着 request_id + thread_id + run_id 三个号.

    三个号来自三处: 中间件 (HTTP 那一层) / 会话层 (装配时) / 记录员 (开账那一刻)
    —— 它们能落在同一行上, 说明这条路是通的.
    """
    server = serve(MockLLM.scripted([two_phase, two_phase]))

    async with client_for(server) as client:
        response = await client.post(
            "/runs",
            json={"message": "跑一下"},
            headers=headers(thread="toy:three", request_id="req-three"),
        )

    session = server.sessions.sessions[0]
    written = probe_lines(log_stream)

    assert written, "探针那一行没写出来"
    for line in written:
        assert line["request_id"] == "req-three"
        assert line["thread_id"] == "toy:three"
        assert line["run_id"] == session.last_run_id
    assert session.last_run_id, "配了记录员就该有运行编号"
    assert response.headers[REQUEST_ID_HEADER] == "req-three"


async def test_a_non_http_scope_passes_straight_through() -> None:
    """非 HTTP 的 scope (lifespan / websocket) 原样放过去.

    「号要还回去」那一半 (`log_context` 出块还原) 的用例在 `test_logging.py` ——
    它的可观察之处在上下文本身, 不必绕到 HTTP 这一层来验.
    """
    seen: list[dict] = []

    async def app(scope, receive, send) -> None:
        seen.append(scope)

    await RequestIdMiddleware(app)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]

    assert seen == [{"type": "lifespan"}], "lifespan 被当请求处理了"
