"""会话历史只读端点 (ASGI 端到端): 刷新之后对话还在, 且只交出该交的那一份.

会话活在**进程内存**里 (按 thread_id 长驻), 浏览器刷新会把页面那一份渲染丢光 ——
这条路由就是那个读口. 用例分三层:

1. **取回来的是对话**: 问一句之后拉历史, 拿到的是 user / assistant 两段; 中间轮的
   叙述 (「我查一下」) 也在, 因为那是模型说过的话.
2. **不该出去的一个都不出去**: system (业务的提示词) 与 tool (工具返回值全文) 被
   滤掉, 事件的其余字段 (reasoning_content / tool_calls) 一个都不复制. 样本里的
   区段名与内部编号只出现在**工具那两处** (参数与返回), 于是「整段响应里搜不到
   它们」就是一条能兑现的断言.
3. **只读**: 写意图 (POST / DELETE) 被挡在门外, 而且**不产生任何运行**、也不建会话
   —— 拉历史这件事不该有副作用.

业务与电商毫无关系 (与 `test_server_app.py` 同一套玩具业务做法): 框架的用例里
出现业务词, 那条「框架不认识业务」的通用性用例会当场红.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.agent import RunContext
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.model.protocol import ChatModel
from CharAgent.server import (
    HISTORY_PATH,
    MESSAGES_FIELD,
    THREAD_ID_FIELD,
    ServerAuthError,
    create_app,
)
from CharAgent.server.history import conversation_of
from CharAgent.tool import Tool, tool

TOKEN = "toy-token"

# 只该出现在工具那两处 (模型填的参数 / 工具返回的正文) 的两个值 —— 历史接口里
# 搜得到它们, 就说明某一类消息漏了出去
SECTION = "湖景"
NOTE = "SEAT-42"


@tool
async def seat_note(section: str) -> str:
    """查询某个区段的内部备注.

    Args:
        section: 区段名.
    """

    return f"{section} 区的内部备注: {NOTE}"


TOY_TOOLS: tuple[Tool, ...] = (seat_note,)


class ToyContexts:
    """插座一: 认证 + 解析 (与 `test_server_app.py` 那份同款, 只认两个头)."""

    async def provide(self, request: httpx.Request) -> RunContext:
        if request.headers.get("X-Toy-Token") != TOKEN:
            raise ServerAuthError()
        user = request.headers.get("X-Toy-User", "anonymous")
        return RunContext(thread_id=f"toy:{user}:1", payload={"user": user})


@dataclass
class ToySessions:
    """插座二: 装配 (模型 / 工具都在这里定)."""

    model: ChatModel
    sessions: list[ChatSession] = field(default_factory=list)

    async def provide(self, context: RunContext, *, event_sink) -> ChatSession:
        session = ChatSession(
            self.model,
            saver=InMemoryCheckpointSaver(),
            tools=TOY_TOOLS,
            thread_id=context.thread_id,
            event_sink=event_sink,
        )
        self.sessions.append(session)
        return session


def headers(*, user: str = "u-9f3a", token: str | None = TOKEN) -> dict[str, str]:
    """玩具业务自己那套头 (框架一个都不认识)."""
    values = {"X-Toy-User": user}
    if token is not None:
        values["X-Toy-Token"] = token
    return values


# 一次「边叙述边调工具」的对话: 中间那轮的 assistant 带着叙述与 tool_calls
ONE_QUESTION = [
    tool_call_response(
        make_tool_call("seat_note", json.dumps({"section": SECTION})),
        content="我查一下。",
        reasoning=f"要不要提 {NOTE}?",
    ),
    text_response("备注我看到了。"),
]


def build(model: ChatModel) -> Any:
    """起一个玩具服务 (ASGI app); 返回它, 用例从 `app.state` 上看两张登记表."""
    return create_app(
        context_provider=ToyContexts(), session_provider=ToySessions(model=model)
    )


async def talk(app: Any, message: str, **header_kwargs: Any) -> httpx.Response:
    """问一句并等它跑完 (响应体里就是整条事件流)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://toy"
    ) as http:
        return await http.post(
            "/runs", json={"message": message}, headers=headers(**header_kwargs)
        )


async def history(
    app: Any, *, method: str = "GET", **header_kwargs: Any
) -> httpx.Response:
    """拉一次历史 (默认 GET; 其余方法用来钉「只读」)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://toy"
    ) as http:
        return await http.request(
            method, HISTORY_PATH, headers=headers(**header_kwargs)
        )


# ---------------------------------------------------------------------------
# 取回来的是对话
# ---------------------------------------------------------------------------


async def test_the_history_comes_back_after_a_question() -> None:
    """问一句 → 拉历史: 拿到这次对话 (问题 + 答复), 顺序与内容都对得上."""
    app = build(MockLLM.scripted(ONE_QUESTION))
    await talk(app, "帮我看看备注")

    response = await history(app)

    assert response.status_code == 200
    payload = response.json()
    assert payload[THREAD_ID_FIELD] == "toy:u-9f3a:1"
    assert payload[MESSAGES_FIELD] == [
        {"role": "user", "content": "帮我看看备注"},
        # 中间轮那句叙述也是模型说过的话, 照留 (调用方按 role 顺序渲染)
        {"role": "assistant", "content": "我查一下。"},
        {"role": "assistant", "content": "备注我看到了。"},
    ]


async def test_a_conversation_that_never_started_is_empty_not_missing() -> None:
    """没聊过的会话编号: 200 + 空列表 —— 「还没聊过」不是错误.

    第一次打开页面就是这种情形. 回 404 的话, 调用方要为一个**正常**情形写分支,
    而那条分支与「对话真的不存在」分不开.
    """
    app = build(MockLLM.scripted(ONE_QUESTION))

    response = await history(app)

    assert response.status_code == 200
    assert response.json() == {THREAD_ID_FIELD: "toy:u-9f3a:1", MESSAGES_FIELD: []}


async def test_asking_for_history_does_not_start_a_conversation() -> None:
    """拉历史**不建会话**: 问一句才建 —— 否则登记的会话表会被只读请求撑大."""
    app = build(MockLLM.scripted(ONE_QUESTION))

    await history(app)

    assert app.state.session_registry.thread_ids == ()


# ---------------------------------------------------------------------------
# 不该出去的一个都不出去
# ---------------------------------------------------------------------------


async def test_the_tool_payload_never_reaches_the_reader() -> None:
    """方案里最要紧的一条: 工具的**参数与返回正文**都不在历史里.

    区段名与内部编号各只出现在工具那两处, 所以这条断言在整段响应体上搜值 (而不是
    逐字段比对) —— 漏了任何一类消息, 它当场就红.
    """
    app = build(MockLLM.scripted(ONE_QUESTION))
    await talk(app, "帮我看看备注")

    response = await history(app)

    for leak in (SECTION, NOTE, "tool_call", "call_seat_note"):
        assert leak not in response.text, f"{leak!r} 漏到了历史接口上"


async def test_only_the_two_roles_come_out() -> None:
    """只剩 user / assistant 两类消息; system (业务提示词) 与 tool 都不出现."""
    app = build(MockLLM.scripted(ONE_QUESTION))
    await talk(app, "帮我看看备注")

    messages = (await history(app)).json()[MESSAGES_FIELD]

    assert {message["role"] for message in messages} <= {"user", "assistant"}
    assert all(set(message) == {"role", "content"} for message in messages)


# ---------------------------------------------------------------------------
# 只读
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_writing_to_the_history_is_refused(method: str) -> None:
    """写意图一律 405 (路由层给的), 而且 `Allow` 里写着只有 GET —— 这是契约.

    看起来像「什么都不做」的一条, 其实是被别处的机制挡下的: 哪天有人顺手把
    `@app.get` 改成 `@app.api_route(methods=[...])`, 这条会替他喊一声.
    """
    app = build(MockLLM.scripted(ONE_QUESTION))

    response = await history(app, method=method)

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"


async def test_a_refused_write_produces_no_run() -> None:
    """被挡下的那次写意图**不产生任何运行** (登记表与运行表都是空的)."""
    app = build(MockLLM.scripted(ONE_QUESTION))

    await history(app, method="POST")

    assert app.state.session_registry.thread_ids == ()
    assert app.state.run_registry.run_ids == ()
    assert app.state.session_registry.busy_threads == frozenset()


# ---------------------------------------------------------------------------
# 认证与隔离 (与 /runs 同一道门)
# ---------------------------------------------------------------------------


async def test_the_reader_goes_through_the_same_door() -> None:
    """认证失败 → 401, 一个字的对话也拿不到 (与 POST /runs 逐字同款)."""
    app = build(MockLLM.scripted(ONE_QUESTION))
    await talk(app, "帮我看看备注")

    response = await history(app, token=None)

    assert response.status_code == 401
    assert "备注" not in response.text


async def test_each_caller_only_sees_its_own_conversation() -> None:
    """换一个身份就换一段对话: 一个人拿不到另一个人的历史.

    判据只有一条 —— 请求里认出来的 `thread_id` (身份在会话编号里). 请求体与查询串
    影响不了它, 所以这里也没什么可伪造的: 拿别人的身份去问, 得到的是**那个人自己**
    的空对话.
    """
    app = build(MockLLM.scripted(ONE_QUESTION))
    await talk(app, "帮我看看备注", user="u-9f3a")

    mine = await history(app, user="u-9f3a")
    other = await history(app, user="u-0000")

    assert [m["content"] for m in mine.json()[MESSAGES_FIELD]] != []
    assert other.json() == {THREAD_ID_FIELD: "toy:u-0000:1", MESSAGES_FIELD: []}


# ---------------------------------------------------------------------------
# 过滤规则本身 (纯函数)
# ---------------------------------------------------------------------------


def test_the_conversation_drops_what_the_model_sees_but_people_do_not() -> None:
    """`conversation_of` 的规则: 只留两类角色、非空正文, 且**逐条新建两个键**.

    最后半句是白名单的意思: assistant 那条上挂着 `reasoning_content` 与
    `tool_calls`, 它们不会被复制过去 —— wire 消息以后再加字段, 默认也进不了
    浏览器, 要放行得回来改这里.
    """
    wire = [
        {"role": "system", "content": "你是玩具助手."},
        {"role": "user", "content": "问一句"},
        {
            "role": "assistant",
            "content": "我查一下。",
            "reasoning_content": "内部推理",
            "tool_calls": [{"id": "call_1", "function": {"name": "seat_note"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": f"内部备注: {NOTE}"},
        {"role": "assistant", "content": "   "},  # 空白正文: 不算一句话
        {"role": "assistant", "content": "答复"},
    ]

    assert conversation_of(wire) == [
        {"role": "user", "content": "问一句"},
        {"role": "assistant", "content": "我查一下。"},
        {"role": "assistant", "content": "答复"},
    ]


def test_the_conversation_of_an_empty_history_is_empty() -> None:
    """空历史 → 空列表 (新会话就是这种情形, 不炸)."""
    assert conversation_of([]) == []


def test_the_display_roles_are_the_two_that_are_a_dialogue() -> None:
    """这两类角色的名字是 wire 契约的一部分 —— 改动它们等于改动过滤规则."""
    from CharAgent.server.history import DISPLAY_ROLES

    assert frozenset({"user", "assistant"}) == DISPLAY_ROLES
