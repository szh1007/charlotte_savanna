"""服务进程: 一次问答真的能通过 HTTP 拿到完整事件流 (PRD §4.9 的 L1b 第一片).

被测的是**接线** —— 从 HTTP 请求头一路走到商城的 HTTP 请求:

    令牌 + X-User-Id → RunContext → 工具 (身份进闭包) → 模型决策 → 工具执行
        → 商城接口 → 数据回填 → 答复 → SSE 事件流

扮演商城的是 respx, 扮演模型的是框架的 MockLLM, 业务代码一行不改; 传输用 httpx
的 ASGI transport (不起服务、不占端口), 整套测试离线可跑 —— 与 `test_cli.py` 同一
套替身、同一套写法.

**同一件事为什么在两页各断一次**: 命令行与 HTTP 是同一段业务的两个入口, 差别只在
两头 (身份从哪来 / 话怎么说出去). 所以两页都有「问余额」「多轮接得上」这类用例,
但断言的对象不同: 那页断的是终端的输出, 这页断的是 SSE 帧与状态码. 而中间那段装配
是同一份代码 —— 有一条用例从两个入口各打一次, 断言落点是同一个函数
(`test_both_entries_go_through_the_same_assembly`), 免得它哪天漂成两份.

真商城 + 真模型上跑过的那一次不在这里 (测试替代不了它), 结论记在 issue 05 的
「实际开发情况」.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from conftest import (
    AGENT_BASE_URL,
    BUYER_ID,
    ORDER_NO,
    PROFILE,
    TOKEN,
    agent_url,
    mock_all,
)
from fastapi import FastAPI

from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.checkpoint import config as checkpoint_config
from CharAgent.model.utils.types import ModelMessage, ModelResponse, Usage
from CharAgent.server import RUN_ID_HEADER
from CharAgent.tests.doubles import FakeRecordDatabase
from CharAgent.tests.mock_llm import (
    MockLLM,
    make_tool_call,
    text_response,
    tool_call_response,
)
from CharAgent.tests.trace_assertions import trace_of
from CharApp.minimall import cli
from CharApp.minimall.client import HEADER_TOKEN, HEADER_USER_ID, MinimallClient
from CharApp.minimall.config import (
    DEFAULT_CONTEXT_KEEP_TURNS,
    DEFAULT_CONTEXT_MAX_TOKENS,
    DEFAULT_CONTEXT_SUMMARY,
    DEFAULT_CONTEXT_TOOL_LIMIT,
    DEFAULT_CONTEXT_WATERMARK,
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
    ENV_BASE_URL,
    ENV_CONTEXT_KEEP_TURNS,
    ENV_CONTEXT_MAX_TOKENS,
    ENV_CONTEXT_SUMMARY,
    ENV_CONTEXT_TOOL_LIMIT,
    ENV_CONTEXT_WATERMARK,
    ENV_SERVER_HOST,
    ENV_SERVER_PORT,
    ENV_THINKING,
    ENV_TOKEN,
    ContextConfig,
    MinimallConfigError,
    ServerConfig,
    context_config_from_env,
    server_config_from_env,
    thinking_from_env,
)
from CharApp.minimall.redaction import TOOL_PHRASES
from CharApp.minimall.server import (
    DEFAULT_CONVERSATION_ID,
    HEADER_CONVERSATION_ID,
    build_service,
    create_minimall_app,
    logger,
    uvicorn_config,
)
from CharApp.minimall.service import TENANT_WEB, MinimallService, build_context

# 测试里给这个服务起的名字: respx 放行这个 host 上的请求 (交给 ASGI app),
# 其余照旧拦给假商城
APP_HOST = "http://charapp"

# 仓库根 `.env.example` (CharApp/tests -> CharApp -> 仓库根)
ENV_TEMPLATE = Path(__file__).resolve().parents[2] / ".env.example"


# ---------------------------------------------------------------------------
# 替身与装配
# ---------------------------------------------------------------------------


def allow_app(mall: respx.MockRouter) -> None:
    """放行打向本服务的请求 (别被 respx 拦下).

    为什么必须显式放行: respx 在全局补丁 httpx「传输该怎么选」, 于是同一套测试里
    两个客户端都会被它经手 —— 打假商城的走注册好的 mock 路由, 打本服务的那条得
    有一条「别拦, 交给 ASGI app」的规则 (`assert_all_mocked` 默认会拦下没注册的
    URL, 表现是打服务时报一句「not mocked」).
    """
    mall.route(host="charapp").pass_through()


def serving(model: Any, client: Any) -> Any:
    """起一个客服服务 (ASGI app): 假商城客户端 + 假模型 + 内存快照.

    与生产同一条装配线上来的: `create_minimall_app` + 一份 `MinimallService`,
    差别只在零件是替身、快照在后端列表里挑了内存那份.
    """
    return create_minimall_app(
        MinimallService(client=client, model=model, saver=InMemoryCheckpointSaver()),
        ServerConfig(host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT, token=TOKEN),
    )


@contextlib.asynccontextmanager
async def talking_to(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """直接打 app 的客户端 (ASGI 传输: 不起服务, 不占端口)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=APP_HOST
    ) as client:
        yield client


def headers(
    *,
    buyer: int | str | None = BUYER_ID,
    token: str | None = TOKEN,
    conversation: str | None = None,
) -> dict[str, str]:
    """业务自己那套头 (框架一个都不认识: 认证与身份都是业务的事).

    值一律 ASCII: HTTP 头的字节按 ASCII 编 (框架的 server 用例踩过这一条), 中文
    身份塞进头里会在客户端就炸掉 —— 这类值该放请求体 (UTF-8).
    """
    values: dict[str, str] = {}
    if token is not None:
        values[HEADER_TOKEN] = token
    if buyer is not None:
        values[HEADER_USER_ID] = str(buyer)
    if conversation is not None:
        values[HEADER_CONVERSATION_ID] = conversation
    return values


async def ask(app: Any, message: str, **header_kwargs: Any) -> httpx.Response:
    """打一次 POST /runs 并等这次运行跑完 (响应体里就是整条事件流)."""
    async with talking_to(app) as http:
        return await http.post(
            "/runs", json={"message": message}, headers=headers(**header_kwargs)
        )


def ask_here(app: Any, message: str, **header_kwargs: Any) -> httpx.Response:
    """同步地问一句 (给同步用例用: 自己开一个循环, 跑完就关)."""
    return asyncio.run(ask(app, message, **header_kwargs))


# ---------------------------------------------------------------------------
# 事件流的读法 (SSE 帧 → 事件字典)
# ---------------------------------------------------------------------------


def parse_sse(body: str) -> list[dict[str, Any]]:
    """SSE 正文 → 逐事件: {"id": 序号, "event": 类型, "data": 载荷}.

    只解析本层产出的形状 (三行一帧 + 空行分隔), 不做通用 SSE 解析 —— 与框架
    `test_server_app.py` 那份同一个读法 (06 的前端按同一份契约读).
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


def as_text(events: Any) -> str:
    """整条流序列化成一段文本 (「里面有没有某个值」这类断言用它)."""
    return json.dumps(events, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 假模型: 先查余额, 再把结果说进答复
# ---------------------------------------------------------------------------


async def balance_dialogue(messages: list[ModelMessage]) -> ModelResponse:
    """假模型: 先查余额, 拿到结果后把余额原样说进答复.

    为什么用「每次调用现算」的对话函数 (配合 `MockLLM.fixed`) 而不是固定脚本:
    并发用例里两个运行的模型调用会交错, 谁先谁后不确定 —— 脚本是按调用顺序发牌的,
    发到谁手上全看调度; 而这个函数只看「本次请求的历史里有没有工具结果」, 于是每个
    运行都能各自走完自己的「先查再答」, 各流里的数据只可能来自各自那次工具调用.
    """
    results = [message for message in messages if message.get("role") == "tool"]
    if not results:
        return tool_call_response(make_tool_call("get_my_profile"))
    balance = json.loads(results[-1]["content"])["balance"]
    return text_response(f"你的余额是 {balance} 元.")


@pytest.fixture
def assemblies(monkeypatch) -> list[str]:
    """记下每一次会话装配 (被装配过的 thread_id), 装配本身照原样走.

    这是几条用例共用的证据口: 「会话按编号复用」「不同编号各建一个」「两个入口
    装配落在同一处」都从这份记录上看. 做法是把 `MinimallService.session_for`
    换成记账的替身 —— 替换的是**记账动作**, 不是装配逻辑.
    """
    recorded: list[str] = []
    original = MinimallService.session_for

    async def spy(self, context, *, event_sink, redact):
        recorded.append(context.thread_id)
        return await original(self, context, event_sink=event_sink, redact=redact)

    monkeypatch.setattr(MinimallService, "session_for", spy)
    return recorded


@pytest.fixture
def service_env(monkeypatch) -> None:
    """钉住生产接线 (`build_service`) 要读的三个变量: 令牌 / 模型 Key / 快照后端.

    模型 Key 只是**建对象**要的最小值 (不真调 API), 所以给个假值就够.
    """
    monkeypatch.setenv(ENV_TOKEN, TOKEN)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-used")
    monkeypatch.setenv(checkpoint_config.ENV_BACKEND, "memory")


@pytest.fixture
def cli_env(monkeypatch) -> None:
    """钉住命令行那半边要读的环境 (与 `test_cli.py` 的 `mall_env` 同一套理由).

    地址不钉会去连本机 8000 的真 Django; 快照后端不钉可能去连真 Redis / Postgres
    (开发者完全可能按框架文档把 `.env` 里的后端换掉) —— 那这两条用例就不再是
    「离线」的.

    记录表那条线同样要钉 (ticket 17 起命令行入口会记账): 这条用例真的跑了一次
    `cli.main`, 不换库就会往真库写一轮 MockLLM 的假对话 (2026-09-22 撞上).
    """
    from CharAgent.tests.doubles import FakeRecordDatabase

    monkeypatch.setenv(ENV_TOKEN, TOKEN)
    monkeypatch.setenv(ENV_BASE_URL, AGENT_BASE_URL)
    monkeypatch.setenv(checkpoint_config.ENV_BACKEND, "memory")
    monkeypatch.setattr(cli, "build_database", FakeRecordDatabase)


# ---------------------------------------------------------------------------
# 一次问答走通 (验收第一条)
# ---------------------------------------------------------------------------


async def test_turning_thinking_off_reaches_the_model(mall, client) -> None:
    """关掉思考模式: 这次请求里带的就是 `thinking=False` (开关真的落到 generate 上).

    为什么值得一条用例: `MinimallService.thinking` 要穿过 `ChatSession` → `AgentLoop`
    才到模型, 中间任何一环漏传, 表现都是「开关解析了、存下了、但没生效」——这种静默
    失效命令行那侧踩过一次 (见 `service.py` 里那段注释)。
    """
    allow_app(mall)
    mock_all(mall)
    model = MockLLM.fixed(balance_dialogue)
    app = create_minimall_app(
        MinimallService(
            client=client,
            model=model,
            saver=InMemoryCheckpointSaver(),
            thinking=False,
        ),
        ServerConfig(host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT, token=TOKEN),
    )

    response = await ask(app, "我余额还有多少")

    assert response.status_code == 200
    assert model.calls[0]["thinking"] is False, "开关没落到 generate 的参数上"


async def test_a_run_that_blows_its_token_budget_is_stopped(mall, client) -> None:
    """一次运行烧超 token 预算 → 刹车 (终局是 `token_budget` 的 error, 不是 final).

    守的是「刹车真接上了」: 三个上限 (轮数 / token / 墙钟) 要穿过 `ChatSession` 才
    到 `AgentLoop` 里的 `LoopGuard`, 中间漏传一环的表现就是**配了没用** —— 照样一次
    跑飞烧一波钱, 而且测试全绿 (与上面那条 thinking 用例同一类静默失效).
    """
    allow_app(mall)
    mock_all(mall)
    # 每轮都报一个远超预算的用量: 跑完第一轮就该被拦下
    model = MockLLM.fixed(
        tool_call_response(
            make_tool_call("get_my_profile"),
            usage=Usage(input_tokens=5_000, output_tokens=5_000),
        )
    )
    app = create_minimall_app(
        MinimallService(
            client=client,
            model=model,
            saver=InMemoryCheckpointSaver(),
            max_total_tokens=1_000,  # 比上面那条用量小一个数量级
        ),
        ServerConfig(host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT, token=TOKEN),
    )

    response = await ask(app, "我余额还有多少")

    assert response.status_code == 200
    errors = [event for event in parse_sse(response.text) if event["event"] == "error"]
    assert errors, "预算都超了还没刹车 —— 上限没接到 LoopGuard 上"
    assert errors[-1]["data"]["error"]["code"] == "token_budget"


async def test_a_run_that_outlives_its_wall_clock_budget_is_stopped(
    mall, client
) -> None:
    """一次运行超过墙钟预算 → 刹车 (终局是 `time_limit` 的 error).

    与 token 那条同一个理由 (上限得真接到 `LoopGuard` 上), 但更难造: 不能真等 90 秒.
    办法是把预算调到 1 微秒 —— 第一轮跑完必然早超了 (判据是 `elapsed >= 预算`,
    而第一轮至少有一次模型调用与一次工具执行), 于是这条用例稳定命中, 不用 sleep.
    """
    allow_app(mall)
    mock_all(mall)
    model = MockLLM.fixed(tool_call_response(make_tool_call("get_my_profile")))
    app = create_minimall_app(
        MinimallService(
            client=client,
            model=model,
            saver=InMemoryCheckpointSaver(),
            max_duration_seconds=1e-6,  # 比"第一轮跑完"小得多, 见 docstring
        ),
        ServerConfig(host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT, token=TOKEN),
    )

    response = await ask(app, "我余额还有多少")

    assert response.status_code == 200
    errors = [event for event in parse_sse(response.text) if event["event"] == "error"]
    assert errors, "墙钟早就超了还没刹车 —— 上限没接到 LoopGuard 上"
    assert errors[-1]["data"]["error"]["code"] == "time_limit"


async def test_a_question_comes_back_as_an_sse_stream(mall, client) -> None:
    """问一句「我余额还有多少」→ SSE 流; 终局事件里是**真实商城的余额**.

    这一条把三个契约一次钉住: 传输是 SSE (media type + 帧格式), 事件序列完整
    (seq 连续、终局恰好一个、每个事件带 run_id), 以及数据是真的 (余额来自假商城
    那份样本, 不是模型编的 —— 工具确实跑了, 身份确实带到了商城那侧).
    """
    allow_app(mall)
    routes = mock_all(mall)
    app = serving(MockLLM.fixed(balance_dialogue), client)

    response = await ask(app, "我余额还有多少")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    run_id = response.headers[RUN_ID_HEADER]
    events = parse_sse(response.text)

    # 序号连续 (SSE 的 id 字段就是事件流的 seq), 终局事件恰好一个
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    assert len(terminal_events(events)) == 1
    assert terminal_events(events)[0]["event"] == "final"
    # 每个事件都带本次运行的编号 (跨 run 定位靠 run_id + seq 的组合)
    assert {event["data"]["run_id"] for event in events} == {run_id}

    # 工具真的跑过: 调用与结果都在流里
    calls = [
        event["data"]["tool_name"] for event in events if event["event"] == "tool_call"
    ]
    assert calls == ["get_my_profile"]
    assert routes["GET profile/"].called, "工具没打到商城 —— 答复里的余额就是编的了"
    assert routes["GET profile/"].calls[0].request.headers[HEADER_USER_ID] == str(
        BUYER_ID
    )

    # 终局事件是权威答复: 里面有商城返回的那个余额
    final = terminal_events(events)[0]["data"]
    assert final["content"] is not None and PROFILE["balance"] in final["content"]


async def test_the_stream_carries_no_backend_fields(mall, client) -> None:
    """浏览器拿到的那条流里**搜不到任何后端字段** (ADR-0003 的验收: 敢当场开 devtools).

    样本挑的是最容易漏的那个工具: 订单号在 `get_my_order` 这条路上同时出现在三处
    (模型填的参数 / 工具返回的正文 / 错误文案), 删漏任何一处这条都红; 收货人、
    金额、状态是同一类东西 —— 它们只该躺在商城与模型的 wire 历史里.

    两条**正向**断言同样是重点: 页面上那两行得换成中文短语, 而 `tool_name` 要留着
    (演示时讲「模型选了哪个工具、有没有选错」全靠它).
    """
    allow_app(mall)
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("get_my_order", f'{{"order_no": "{ORDER_NO}"}}')
            ),
            text_response("订单查到了。"),
        ]
    )

    response = await ask(serving(model, client), "我最近的订单到哪了")

    assert response.status_code == 200
    assert routes[f"GET orders/{ORDER_NO}/"].called, "工具没真跑, 后面几条就都空了"
    for leak in (ORDER_NO, "张三", "1899.00", "shipped"):
        assert leak not in response.text, f"{leak!r} 漏到了 SSE 流上"

    tool_events = [
        event["data"]
        for event in parse_sse(response.text)
        if event["event"] in ("tool_call", "tool_result")
    ]
    assert tool_events, "工具调用与结果都该在流里 (脱敏不是删事件)"
    for data in tool_events:
        assert "arguments" not in data and "summary" not in data and "error" not in data
    assert [data["label"] for data in tool_events] == [
        TOOL_PHRASES["get_my_order"].calling,
        TOOL_PHRASES["get_my_order"].done,
    ]
    assert {data["tool_name"] for data in tool_events} == {"get_my_order"}


async def test_the_conversation_can_be_read_back(mall, client) -> None:
    """浏览器刷新之后能不能把这段对话拿回来 —— 走业务这一侧的同一个 app.

    这条只证**接线**: 历史端点随框架的 `create_app` 一起装到本业务的服务上, 认证
    用的是业务那两个头 (框架不认识它们). 过滤规则本身 (哪些角色、哪些字段出去)
    归框架的用例, 这里顺带钉一句最要紧的: 工具返回的正文不在历史里 —— 刷新页面
    不能变成一条绕过脱敏的路.
    """
    allow_app(mall)
    mock_all(mall)
    app = serving(MockLLM.fixed(balance_dialogue), client)
    await ask(app, "我余额还有多少")

    async with talking_to(app) as http:
        response = await http.get("/history", headers=headers())

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[0] == {"role": "user", "content": "我余额还有多少"}
    assert messages[-1]["role"] == "assistant"
    for leak in (PROFILE["phone"], PROFILE["email"], PROFILE["username"]):
        assert leak not in response.text, f"{leak!r} 从历史接口漏了出去"


async def test_the_web_entry_has_the_guardrail_too(mall, client) -> None:
    """网页入口同样装着护栏: 第 9 次写操作被拒, 商城只收到 8 次请求.

    为什么非要在 server 这侧再证一遍 (CLI 那侧已经有一条): 护栏是**业务**的插件,
    而"装错地方"的写法太自然了 —— 在 `cli.build_session` 里加一行就"跑通了",
    网页版却是裸奔的, 而 CLI 用例照样全绿. 「装配只有一处」这句话靠的就是两个入口
    各有一条这样的用例 (`test_both_entries_go_through_the_same_assembly` 断的是
    走同一个函数, 这条断的是**那个函数真的把闸装上了**).
    """
    allow_app(mall)
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("add_to_cart", f'{{"slug": "p{n}"}}', call_id=f"c{n}")
            )
            for n in range(1, 10)  # 9 次写操作, 预算 8
        ]
        + [text_response("这件我先不动了, 你到页面上操作吧.")]
    )

    response = await ask(serving(model, client), "把这些都加上")

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert routes["POST cart/items/"].call_count == 8, "第 9 次不该打出去"
    results = [event["data"] for event in events if event["event"] == "tool_result"]
    assert len(results) == 9
    # 被拒的那一次分两个通道各说各的: 事件流上是一句「没成功」(页面看到的), 而
    # **那句理由** (预算用完了) 回填给了模型 —— 买家从答复里听到原因, 浏览器里不留
    # 内部文案 (脱敏见 redaction.py). 两边都要钉, 少一半就成了「用户什么都听不到」
    assert results[-1]["status"] == "error"
    assert "已经用完" not in results[-1].get("label", "")
    backfilled = [
        message
        for message in model.calls[-1]["messages"]
        if message.get("role") == "tool" and "已经用完" in str(message.get("content"))
    ]
    assert backfilled, "护栏给的理由没回填给模型 —— 买家就无从听说了"
    assert terminal_events(events)[0]["event"] == "final", "拒绝不是崩溃"


# ---------------------------------------------------------------------------
# 认证与身份 (验收第二、三条)
# ---------------------------------------------------------------------------


async def test_a_missing_token_and_a_wrong_token_look_the_same(mall, client) -> None:
    """令牌没带 / 带错了: 都拒绝, 而且**两句话逐字相同** (不泄漏是哪种错).

    与商城侧 fail closed 同一套口径: 认不出来就拒绝, 且不告诉调用方「你是令牌错还是
    没带令牌」—— 想区分的是日志, 不是响应.
    """
    allow_app(mall)
    mock_all(mall)
    app = serving(MockLLM.fixed(balance_dialogue), client)

    missing = await ask(app, "我余额还有多少", token=None)
    wrong = await ask(app, "我余额还有多少", token="not-the-token")

    for response in (missing, wrong):
        assert response.status_code == 401
        assert response.json() == {
            "error": {"code": "unauthorized", "message": "认证失败"}
        }
    assert missing.json() == wrong.json(), "两种失败在响应上必须分不出来"


async def test_a_request_without_a_buyer_is_refused_explicitly(mall, client) -> None:
    """缺身份 (或身份不成形): **明确拒绝**, 不是「查不到数据」.

    身份是装配期的输入 —— 它缺了就是这次请求不完整, 不该伪装成一次业务结果
    (「商城没有这笔数据」会让转发方去找商城的麻烦, 而真正的问题在自己这儿).
    拒绝时连商城一个请求都不该发出去.
    """
    allow_app(mall)
    routes = mock_all(mall)
    app = serving(MockLLM.fixed(balance_dialogue), client)

    missing = await ask(app, "我余额还有多少", buyer=None)
    malformed = await ask(app, "我余额还有多少", buyer="abc")

    for response in (missing, malformed):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_identity"
        assert "X-User-Id" in response.json()["error"]["message"], (
            "拒绝的理由要说清是「缺身份」, 转发方才知道该补什么"
        )
    assert not routes["GET profile/"].called, "身份不明时不该拿任何人的数据去查商城"
    assert "余额" not in missing.text, "别把装配期的错伪装成业务结果"


# ---------------------------------------------------------------------------
# 会话: 同一段对话连得上, 不同买家不串台 (验收第四、五条)
# ---------------------------------------------------------------------------


async def test_the_second_question_on_the_same_conversation_sees_the_first(
    mall, client, assemblies
) -> None:
    """同一段对话连问两句: 第二句看得到第一句 (会话按 thread_id 长驻).

    这是 HTTP 上最容易做错的一条 —— 每个请求新建一个会话的话, 第二轮就看不到第一
    轮说过什么 (对话历史在会话对象的内存里). 两条证据一起看: 会话**只装配了一次**
    (复用而不是重建), 以及模型第二次收到的请求里确实带着第一句与它的答复.
    """
    allow_app(mall)
    mock_all(mall)
    model = MockLLM.fixed(balance_dialogue)
    app = serving(model, client)

    first = await ask(app, "我余额还有多少")
    second = await ask(app, "刚才那个数是多少来着")

    assert first.status_code == second.status_code == 200
    assert assemblies == [f"minimall:{BUYER_ID}:{DEFAULT_CONVERSATION_ID}"], (
        "同一段对话的第二次提问不该再装配一个新会话"
    )
    # 第 3 次模型调用 = 第二句的第一次决策 (前两次是「查 → 答」)
    seen = [str(message.get("content") or "") for message in trace_of(model).seen(3)]
    assert any(PROFILE["balance"] in text for text in seen), (
        "第二句的请求里没有第一句的答复 —— 会话没复用"
    )
    assert any(message.get("role") == "tool" for message in trace_of(model).seen(3)), (
        "第一轮的工具结果也不在历史里"
    )


async def test_two_buyers_at_once_each_get_their_own_data(mall, client) -> None:
    """两个买家并发: 各自拿各自的数据, 不串 (会话按 thread_id 分区).

    假商城对两个买家回**不同**的余额 (靠请求头认人), 于是「哪条流里是哪个数字」
    就能证明身份没有串台 —— 如果身份在某一环丢了或被覆盖, 这里会立刻看见两个人
    读到同一个余额.

    为什么还要断言「另一个买家的数字不在我的流里」: 只断言「我的数字在」的话,
    两条流都被回填了同一个余额也能过.
    """
    allow_app(mall)
    mall.get(agent_url("profile/"), headers__contains={HEADER_USER_ID: "3"}).mock(
        return_value=httpx.Response(200, json={**PROFILE, "balance": "300.00"})
    )
    mall.get(agent_url("profile/"), headers__contains={HEADER_USER_ID: "4"}).mock(
        return_value=httpx.Response(200, json={**PROFILE, "id": 4, "balance": "400.00"})
    )
    app = serving(MockLLM.fixed(balance_dialogue), client)

    mine, theirs = await asyncio.gather(
        ask(app, "我余额还有多少", buyer=3),
        ask(app, "我余额还有多少", buyer=4),
    )

    assert "300.00" in as_text(parse_sse(mine.text))
    assert "400.00" not in as_text(parse_sse(mine.text)), "别串到别人的余额"
    assert "400.00" in as_text(parse_sse(theirs.text))
    assert "300.00" not in as_text(parse_sse(theirs.text))


async def test_one_buyers_two_tabs_are_two_conversations(
    mall, client, assemblies
) -> None:
    """同一买家两个标签页: 各聊各的 (会话编号的第三段把它们分开).

    06 的客服页面会给每个标签页发一个 `X-Conversation-Id`, 服务端**一行不用改**:
    它进的就是 `thread_id` 的第三段. 没带这个头时兜默认值, 于是「一个买家一段
    对话」是缺省行为 —— curl 打进来 (验收第一条那种打法) 不必编一个对话 ID.
    """
    allow_app(mall)
    mock_all(mall)
    app = serving(MockLLM.fixed(balance_dialogue), client)

    answers = [
        await ask(app, "我余额还有多少"),
        await ask(app, "我余额还有多少", conversation="tab-1"),
        await ask(app, "我余额还有多少", conversation="tab-2"),
        await ask(app, "我余额还有多少", conversation="tab-1"),
    ]

    for response in answers:
        assert response.status_code == 200
        assert "final" in {event["event"] for event in parse_sse(response.text)}
    assert assemblies == [
        f"minimall:{BUYER_ID}:{DEFAULT_CONVERSATION_ID}",  # 没带头 → 缺省那一段
        f"minimall:{BUYER_ID}:tab-1",
        f"minimall:{BUYER_ID}:tab-2",
    ], "每个标签页一段对话 (第三次问 tab-1 复用, 所以只有三条)"
    assert app.state.session_registry.busy_threads == frozenset(), "跑完了就该全放开"
    assert DEFAULT_CONVERSATION_ID == "web", "缺省那一段是转发层在用的, 改它要同步文档"


async def wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    """等一个条件成立 (轮询 + 超时) —— 与框架 `test_server_app.py` 那份同一个写法.

    「等到某件事发生」在事件循环里没有现成原语, 而取消的用例**必须**等: 取消的前提
    是那次运行真的已经跑起来了 (否则手上没有 run_id).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "等待超时: 条件一直没成立"
        await asyncio.sleep(0)


async def cancel(app: Any, run_id: str, **header_kwargs: Any) -> httpx.Response:
    """打一次取消请求 (`POST /runs/{id}/cancel`); 头与 `ask` 同源, 不另配一套."""
    async with talking_to(app) as http:
        return await http.post(
            f"/runs/{run_id}/cancel", headers=headers(**header_kwargs)
        )


def stalled(gate: asyncio.Event, answer: ModelResponse) -> Callable:
    """一段「闸门不开就不作答」的模型步骤 (让运行停在半路, 好被取消)."""

    async def step(messages: list[ModelMessage]) -> ModelResponse:
        await gate.wait()
        return answer

    return step


# ---------------------------------------------------------------------------
# 取消 (07): 业务侧一行新代码都没有, 端点就已经受同一道门保护
# ---------------------------------------------------------------------------


async def test_a_cancel_request_stops_the_run_through_the_service_headers(
    mall, client
) -> None:
    """取消一次运行: 走的就是业务自己的那两个头 (令牌 + 买家 + 对话编号).

    这条用例本身就是「04 的接缝设计对了」的证据: `create_minimall_app` 只交出两个
    插座, 而框架的取消端点照样认得出「这是谁的哪段会话」—— 靠的是同一个
    `MinimallContexts`. 接缝要是错了 (比如取消只认 run_id), 这里连门都进不来.

    停止的**权威信号**是事件流上那个终局事件 (取消是协作式的, 落在下一个 await):
    所以断言的是「流以 cancelled 收尾」, 而不是「响应体里写着已停止」.
    """
    allow_app(mall)
    mock_all(mall)
    gate = asyncio.Event()
    app = serving(MockLLM.fixed(stalled(gate, text_response("迟到的答复"))), client)

    pending = asyncio.create_task(ask(app, "我余额还有多少"))
    await wait_until(lambda: bool(app.state.run_registry.run_ids))
    [run_id] = app.state.run_registry.run_ids

    response = await cancel(app, run_id)

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
    events = parse_sse((await pending).text)
    assert [event["event"] for event in terminal_events(events)] == ["error"]
    assert events[-1]["data"]["error"]["code"] == "cancelled"
    assert "迟到的答复" not in as_text(events), "停下的运行不该再吐出答复"
    assert app.state.run_registry.run_ids == (), "取消之后要出册"
    assert app.state.session_registry.busy_threads == frozenset(), (
        "会话要放开 —— 不然用户接不上下一句 (「继续」会被 409 拒掉)"
    )


async def test_only_the_conversation_that_started_it_can_cancel_it(
    mall, client
) -> None:
    """取消的判据是**会话编号** (里面含买家), 不是 run_id 本身.

    三种「不是他那一段」都试一遍 (另一个买家 / 同一个买家的另一个标签页 / 没带令牌),
    每次都要原封不动 —— 运行照跑, 而它本人照样取消得动.
    """
    allow_app(mall)
    mock_all(mall)
    gate = asyncio.Event()
    app = serving(MockLLM.fixed(stalled(gate, text_response("答完了"))), client)

    pending = asyncio.create_task(
        ask(app, "我余额还有多少", buyer=3, conversation="tab-1")
    )
    await wait_until(lambda: bool(app.state.run_registry.run_ids))
    [run_id] = app.state.run_registry.run_ids

    for note, header_kwargs in (
        ("另一个买家", {"buyer": 4, "conversation": "tab-1"}),
        ("同一个买家的另一个标签页", {"buyer": 3, "conversation": "tab-2"}),
    ):
        refused = await cancel(app, run_id, **header_kwargs)

        assert refused.status_code == 404, note
        assert app.state.run_registry.run_ids == (run_id,), f"{note}: 不该动到运行"

    assert (await cancel(app, run_id, buyer=3, conversation="tab-1")).status_code == 200
    assert parse_sse((await pending).text)[-1]["data"]["error"]["code"] == "cancelled"


async def test_a_cancel_request_without_a_token_is_refused(mall, client) -> None:
    """没带令牌: 401 —— 取消端点进的是**同一道门**, 不是另一套认证.

    这条与 `test_a_missing_token_and_a_wrong_token_look_the_same` 是同一件事的另
    一个入口: 同一次运行, 换个端点来敲, 门禁的表现应当逐字相同 (连响应体都一样).
    """
    allow_app(mall)
    mock_all(mall)
    gate = asyncio.Event()
    app = serving(MockLLM.fixed(stalled(gate, text_response("答完了"))), client)

    pending = asyncio.create_task(ask(app, "我余额还有多少"))
    await wait_until(lambda: bool(app.state.run_registry.run_ids))
    [run_id] = app.state.run_registry.run_ids

    response = await cancel(app, run_id, token=None)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert app.state.run_registry.run_ids == (run_id,), "没认下来的请求不该动到运行"

    gate.set()
    await pending


# ---------------------------------------------------------------------------
# 装配只有一处 (验收: 可断言的证据)
# ---------------------------------------------------------------------------


def test_both_entries_go_through_the_same_assembly(mall, cli_env, assemblies) -> None:
    """两个入口的会话装配落在**同一个函数**上, 不是两份长得像的代码.

    做法: 从两个入口各问一句 —— 命令行那半直接调 `cli.main`, HTTP 那半打 ASGI
    app —— 两边都在同一份记账替身上留下痕迹, 就说明中间那段装配确实只有一处;
    哪天谁抄了一份走, 这里会少一条记录.

    (抄一份的代价这个项目已经付过一次: 交互层当初被抄走, 抄完就开始漂.)

    为什么这条是**同步**用例: `cli.main` 自己建常驻事件循环 (`KillSwitch` 那一套),
    在别人已经跑着的循环里调它会被 asyncio 拦下 ("another loop is running") ——
    与 `test_cli.py` 那批用例同一个理由, 测试不必也不该插手它的事件循环. HTTP 那
    半自己开一个循环跑 (`ask_here`), 两边各跑各的.
    """
    allow_app(mall)
    mock_all(mall)
    mall_client = MinimallClient(base_url=AGENT_BASE_URL, token=TOKEN)
    app = serving(MockLLM.fixed(balance_dialogue), mall_client)

    code = cli.main(
        ["--user-id", str(BUYER_ID), "-q", "我余额还有多少"],
        model=MockLLM.fixed(balance_dialogue),
    )
    response = ask_here(app, "我余额还有多少")

    assert code == 0 and response.status_code == 200
    assert assemblies == [
        f"minimall:{BUYER_ID}:cli",  # 命令行: 命令行参数 → 上下文
        f"minimall:{BUYER_ID}:{DEFAULT_CONVERSATION_ID}",  # HTTP: 转发头 → 上下文
    ], "两个入口的装配必须都经过 MinimallService.session_for"


# ---------------------------------------------------------------------------
# 收尾与配置
# ---------------------------------------------------------------------------


async def test_closing_the_service_closes_the_process_level_parts(mall, client) -> None:
    """收尾: 谁建谁关 —— 关掉服务就把商城连接池一起关了.

    为什么不在会话上收尾: 会话与别的会话**共用**这三件进程级资源, 而
    `ChatSession.aclose()` 会把模型与存储一起关掉 —— 关一个会话等于顺手关了别人的.
    所以收尾落在服务这一层, 一条进程关一次.
    """
    allow_app(mall)
    mock_all(mall)
    service = MinimallService(
        client=client,
        model=MockLLM.fixed(text_response("好的")),
        saver=InMemoryCheckpointSaver(),
    )

    await service.aclose()

    with pytest.raises(RuntimeError, match="closed"):
        await client.get_profile(user_id=BUYER_ID)


async def test_a_service_without_a_configured_token_refuses_everything(
    mall, client
) -> None:
    """服务端没配令牌 (空串): 一律拒绝 —— 不给「忘了配就放行」留口子.

    与商城侧 `IsInternalService` 同一条 (fail closed). 生产里根本走不到这里
    (`build_service` 在建零件时就抛, 见下一条), 这一条守的是「万一有别的路径把它
    空着送进来, 请求层也不会放行」. 打的令牌是**对的**那个值, 区别只在服务端手里
    没有期望值可比.
    """
    allow_app(mall)
    mock_all(mall)
    app = create_minimall_app(
        MinimallService(
            client=client,
            model=MockLLM.fixed(balance_dialogue),
            saver=InMemoryCheckpointSaver(),
        ),
        ServerConfig(host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT, token=""),
    )

    response = await ask(app, "我余额还有多少", token=TOKEN)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_build_service_wires_the_process_level_parts(
    mall, client, service_env, monkeypatch
) -> None:
    """生产接线: 环境变量 → 三件零件 → 能装配出会话 → 能收尾.

    为什么单独测这一小段: 上面所有 app 用例都是把 `MinimallService` 直接注进去的,
    生产那条路 (env → `build_service` → `create_minimall_app` → uvicorn) 接错了,
    那些用例一条都不会红. 这里不碰商城也不碰模型 (只建对象), 断的是「接线」.
    """
    monkeypatch.setenv(ENV_THINKING, "no")  # 顺手断「思考模式开关也接上了」
    monkeypatch.setenv(ENV_CONTEXT_KEEP_TURNS, "4")  # 顺手断「压缩旋钮也接上了」
    service = build_service(writer=logger.info)

    context = build_context(BUYER_ID, "web", tenant_id=TENANT_WEB)
    session = asyncio.run(
        service.session_for(context, event_sink=lambda event: None, redact=True)
    )

    assert isinstance(service.client, MinimallClient)
    assert session.thread_id == context.thread_id
    assert len(session.tool_names) == 17, "零件接全了: 工具从上下文里装了出来"
    assert service.thinking is False, "env 里的思考模式开关没接到零件上"
    assert service.compaction is not None, "压缩旋钮没接到零件上 (env → ContextConfig)"
    assert service.compaction.keep_turns == 4, "接到零件上的还是默认值, 不是 env 那个"
    asyncio.run(service.aclose())


def test_the_process_reports_a_missing_token_in_one_line(monkeypatch, capsys) -> None:
    """令牌没配: 进程入口报一句人话 + 退出码 1 (**不是** traceback), 也不占端口.

    这一条断的是「启动期错误被翻成人话」这条路 —— 会抛的是同一批错误 (共用
    `STARTUP_ERRORS`), 该说的也是同一句话.

    为什么先把 handler 清掉: 日志口是 `_configure_logging` 挂的, 而它挂上就不再
    换 —— 上一个用例挂的那个指着当时的 stderr, `capsys` 抓不到. 清一次, 让
    `main` 重新挂一个指向当前 stderr 的.
    """
    from CharApp.minimall import server as server_module

    server_module.logger.handlers.clear()
    # 空值 = 没配: dotenv 不覆盖已存在的键, 所以真 .env 里那个值不会把它填回来
    monkeypatch.setenv(ENV_TOKEN, "")

    assert server_module.main() == 1
    assert "启动失败" in capsys.readouterr().err


def test_the_process_reports_a_bad_context_knob_in_one_line(
    monkeypatch, capsys, service_env
) -> None:
    """压缩旋钮写坏了 → 一句中文 + 退出码 1, 进程不占端口 (与缺令牌同一条路).

    这条同时钉住「越界的值在业务这一层就拦下」: 交给框架去发现的话, 冒出来的是一屏
    traceback (`CompactionConfigError` 不在 `STARTUP_ERRORS` 里), 而写错配置的人只
    想知道是哪一行.
    """
    from CharApp.minimall import server as server_module

    server_module.logger.handlers.clear()
    monkeypatch.setenv(ENV_CONTEXT_WATERMARK, "1.5")

    assert server_module.main() == 1
    err = capsys.readouterr().err
    assert "启动失败" in err
    assert ENV_CONTEXT_WATERMARK in err, "那句人话要说清是哪一行配置写坏了"


def test_the_server_config_comes_from_the_env(monkeypatch) -> None:
    """监听地址与端口从 `CHARAPP_SERVER_*` 读; 没配就用本机 1007."""
    monkeypatch.setenv(ENV_TOKEN, TOKEN)
    monkeypatch.setenv(ENV_SERVER_HOST, "0.0.0.0")
    monkeypatch.setenv(ENV_SERVER_PORT, "9105")

    config = server_config_from_env()

    assert (config.host, config.port) == ("0.0.0.0", 9105)
    assert config.token == TOKEN, "校验令牌与打商城用的是同一个值"


def test_the_uvicorn_settings_differ_from_the_defaults_only_in_the_access_log() -> None:
    """uvicorn 的三个设置: 访问日志**关着**, 另两个照默认走 (issue 29).

    访问日志为什么必须关: 它记的是「每条请求的 URL 与状态码」, 而列会话那条路的搜索词
    走查询串 (`?q=`, 见 `views_bff` 的 `UPSTREAM_QUERY_FIELD`), 它完全可能是一个订单号
    —— 前端特意把搜索词放进请求体, 正是这个理由 (到了这最后一跳没有别的形状可放).
    关掉它看着只是一行配置, 其实是**一次性盖住所有**将来被带进 URL 的值的处置.

    另两句断的是「这条改动没顺手带偏别的」: 监听地址照传, 日志级别照旧 (启动那一句
    人话与 uvicorn 自己的错误都靠它).
    """
    config = uvicorn_config(
        FastAPI(), ServerConfig(host="127.0.0.1", port=1007, token="tok")
    )

    assert config.access_log is False, "访问日志会记下整条 URL (含 ?q= 里的搜索词)"
    assert config.log_level == "info", "启动那一句人话与 uvicorn 自己的错误还靠它"
    assert (config.host, config.port) == ("127.0.0.1", 1007), "听哪儿照旧透传"


def test_the_server_config_requires_a_token(monkeypatch) -> None:
    """令牌没配 → 启动期错误 (进程起不来), 而不是先跑起来再逐个请求拒绝."""
    monkeypatch.delenv(ENV_TOKEN, raising=False)

    with pytest.raises(MinimallConfigError, match=ENV_TOKEN):
        server_config_from_env()


def test_the_thinking_switch_is_three_state(monkeypatch) -> None:
    """思考模式是**三态**: 不填 = 不传 (上游默认开启) / 开 / 关 —— 空值不等于关.

    「不填」必须与「填了 false」区分开: 前者是让上游自己决定 (今天的行为),
    后者是明确关掉. 混成一态的话, 不填就从「上游默认」变成「我们替上游决定」.
    """
    monkeypatch.delenv(ENV_THINKING, raising=False)
    assert thinking_from_env() is None, "不填 = 不传该参数"

    for value in ("false", "0", "no", "off", " FALSE "):
        monkeypatch.setenv(ENV_THINKING, value)
        assert thinking_from_env() is False, f"{value!r} 应读成「关」"

    for value in ("true", "1", "yes", "on", "ON"):
        monkeypatch.setenv(ENV_THINKING, value)
        assert thinking_from_env() is True, f"{value!r} 应读成「开」"

    # 写错了当场报启动期错误, 而不是替使用者猜一个方向 (它决定 token 与延迟)
    monkeypatch.setenv(ENV_THINKING, "flase")
    with pytest.raises(MinimallConfigError, match=ENV_THINKING):
        thinking_from_env()


def test_the_context_knobs_all_have_defaults(monkeypatch) -> None:
    """五个旋钮**全有默认值**: 一个都不填也跑得起来 (与 `CHARAPP_THINKING` 同一语义).

    「不填」在这里必须等于「用默认那套」而不是「关掉压缩」—— 前者是照常跑, 后者是
    悄悄退化成每轮重发全量历史 (一个只有跑了很久才看得出来的差别).
    """
    for name in (
        ENV_CONTEXT_MAX_TOKENS,
        ENV_CONTEXT_KEEP_TURNS,
        ENV_CONTEXT_TOOL_LIMIT,
        ENV_CONTEXT_SUMMARY,
        ENV_CONTEXT_WATERMARK,
    ):
        monkeypatch.delenv(name, raising=False)

    config = context_config_from_env()

    assert config == ContextConfig(
        max_tokens=DEFAULT_CONTEXT_MAX_TOKENS,
        keep_turns=DEFAULT_CONTEXT_KEEP_TURNS,
        tool_limit=DEFAULT_CONTEXT_TOOL_LIMIT,
        summary=DEFAULT_CONTEXT_SUMMARY,
        watermark=DEFAULT_CONTEXT_WATERMARK,
    ), "默认值应当是 ContextConfig 声明的那几个 (两处不许各写一份)"

    # 空串与没配等价 (dotenv 里 `X=""` 是常见写法)
    monkeypatch.setenv(ENV_CONTEXT_SUMMARY, "")
    assert context_config_from_env().summary is True

    # 填了就读填的那个
    monkeypatch.setenv(ENV_CONTEXT_MAX_TOKENS, "1500")
    monkeypatch.setenv(ENV_CONTEXT_KEEP_TURNS, "2")
    monkeypatch.setenv(ENV_CONTEXT_TOOL_LIMIT, "50")
    monkeypatch.setenv(ENV_CONTEXT_SUMMARY, "off")
    monkeypatch.setenv(ENV_CONTEXT_WATERMARK, "0.3")

    assert context_config_from_env() == ContextConfig(
        max_tokens=1500, keep_turns=2, tool_limit=50, summary=False, watermark=0.3
    )


def test_a_bad_context_knob_is_refused_with_a_human_line(monkeypatch) -> None:
    """坏值在**读配置这一处**就报 (带上变量名与实际值), 不留给框架去发现.

    为什么必须在这里拦: 越界的值交给框架会抛 `CompactionConfigError`, 而它**不在**
    `STARTUP_ERRORS` 那张表里 —— 那意味着 traceback 糊一屏, 而配置写错的人要的只是
    「哪一行写错了」. 水位线是这条规矩最典型的一个 (它必须在 0 与 1 之间).
    """
    for value in ("1.5", "0", "-0.2", "1"):
        monkeypatch.setenv(ENV_CONTEXT_WATERMARK, value)
        with pytest.raises(MinimallConfigError, match=ENV_CONTEXT_WATERMARK):
            context_config_from_env()

    monkeypatch.setenv(ENV_CONTEXT_WATERMARK, "不是数字")
    with pytest.raises(MinimallConfigError, match=ENV_CONTEXT_WATERMARK):
        context_config_from_env()

    monkeypatch.delenv(ENV_CONTEXT_WATERMARK, raising=False)
    for name in (
        ENV_CONTEXT_MAX_TOKENS,
        ENV_CONTEXT_KEEP_TURNS,
        ENV_CONTEXT_TOOL_LIMIT,
    ):
        for value in ("0", "-1", "abc"):
            monkeypatch.setenv(name, value)
            with pytest.raises(MinimallConfigError, match=name):
                context_config_from_env()
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv(ENV_CONTEXT_SUMMARY, "flase")
    with pytest.raises(MinimallConfigError, match=ENV_CONTEXT_SUMMARY):
        context_config_from_env()


def test_the_server_config_falls_back_and_rejects_a_bad_port(monkeypatch) -> None:
    """没配就用默认值; 端口写成非数字当场报启动期错误 (别丢给 uvicorn 猜)."""
    monkeypatch.setenv(ENV_TOKEN, TOKEN)
    monkeypatch.delenv(ENV_SERVER_HOST, raising=False)
    monkeypatch.delenv(ENV_SERVER_PORT, raising=False)

    config = server_config_from_env()

    assert (config.host, config.port) == (DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT)

    monkeypatch.setenv(ENV_SERVER_PORT, "不是我")
    with pytest.raises(MinimallConfigError, match=ENV_SERVER_PORT):
        server_config_from_env()


def test_the_template_lists_every_variable_the_business_reads() -> None:
    """模板里写明了业务读的每个变量名 (使用者唯一能照抄的清单).

    与框架的 `test_env_template.py` 同一条理由: 真 `.env` 不提交, 模板缺一个名字,
    使用者就只能翻源码. 名字从配置模块的常量取 (而不是在源码里搜字符串).
    """
    text = ENV_TEMPLATE.read_text(encoding="utf-8")

    for name in (
        ENV_TOKEN,
        ENV_BASE_URL,
        ENV_SERVER_HOST,
        ENV_SERVER_PORT,
        ENV_THINKING,
        ENV_CONTEXT_MAX_TOKENS,
        ENV_CONTEXT_KEEP_TURNS,
        ENV_CONTEXT_TOOL_LIMIT,
        ENV_CONTEXT_SUMMARY,
        ENV_CONTEXT_WATERMARK,
    ):
        assert name in text, f".env.example 缺少 {name}"


# ---------------------------------------------------------------------------
# 记录表那条线在业务这一侧的样子 (ticket 17)
# ---------------------------------------------------------------------------


def serving_with_records(model: Any, client: Any, records: Any) -> Any:
    """与 `serving` 同一个 app, 只是**接上了记录表** (装配时给了库).

    差别只有一处, 而那一处决定了 `/history` 读哪儿: 框架按 `database=` 选来源
    (给了库读记录表, 没给读会话内存). 生产两个入口都走这条 —— 见 `build_service`.
    """
    return create_minimall_app(
        MinimallService(
            client=client,
            model=model,
            saver=InMemoryCheckpointSaver(),
            database=records,
        ),
        ServerConfig(host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT, token=TOKEN),
    )


async def test_the_web_history_reads_the_record_table(mall, client) -> None:
    """网页端的 `/history` 读**记录表** —— 写进去的那一轮, 刷新之后原样读得回来.

    为什么单列一条: 上面那条 `test_the_conversation_can_be_read_back` 走的是「没配
    记录表」的装配 (读会话内存), 而生产两个入口**都**配了库. 这条把那条路走通:
    问一句 (记录员写进库) → 拉历史 (从库里读回), 中间不经过会话对象.
    """
    allow_app(mall)
    mock_all(mall)
    records = FakeRecordDatabase()
    app = serving_with_records(MockLLM.fixed(balance_dialogue), client, records)

    await ask(app, "我余额还有多少")
    async with talking_to(app) as http:
        response = await http.get("/history", headers=headers())

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[0] == {"role": "user", "content": "我余额还有多少"}
    assert messages[-1]["role"] == "assistant"
    # 而它确实是从库里读的: 记录表里写着这一问一答
    written = [row["content"] for row in records.rows_of("charagent_messages")]
    assert "我余额还有多少" in written
    # 工具那几行 (带工具调用的中间轮 / 工具回填) 是隐藏的 —— 读回来时被过滤掉
    assert all(row["role"] in {"user", "assistant"} for row in messages), (
        "可见的只有问答两方"
    )


async def test_the_tenant_comes_from_the_entry_not_from_the_request(
    mall, client
) -> None:
    """租户由**入口**定死 (网页端 = `TENANT_WEB`), 请求影响不了它.

    框架按 (租户, 属主) 列出会话, 于是「命令行里聊的不出现在买家左栏」靠的就是
    这一行 —— 想伪造也伪造不了: 请求头里没有租户这个字段.
    """
    allow_app(mall)
    mock_all(mall)
    records = FakeRecordDatabase()
    app = serving_with_records(MockLLM.fixed(text_response("好的")), client, records)

    await ask(app, "在吗", conversation="web")

    [thread] = records.rows_of("charagent_threads")
    assert thread["tenant_id"] == TENANT_WEB
    assert thread["user_id"] == str(BUYER_ID)
