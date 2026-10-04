"""逐句引用 (L5-c): 编号、片段、以及「直播与历史是同一份」.

四组:

1. **解析与编号** (纯函数): 一次检索的返回文本 → 引用条目; 编号是**这段对话**的
   序号 (累加不重来), 恢复旧会话时从上一段接着数.
2. **出口**: `final` 上挂引用, 别的事件一个字节不动.
3. **端到端**: 问一句 (模型调 `search_knowledge` 再答) → 事件流里那个 `final`
   带引用 → **同一段对话的 `/history` 回读出逐字一样的引用** (刷新页面那条路).
4. **历史那半边**: 记录行 → 每条消息该带的引用 (只有 assistant 行、只有有 run 的行).

第 3 组是这一片最要紧的一条: 「刷新之后引用还在」有两个写入口 (直播 / 历史重建),
它们是两条代码路径 —— 有一条用例把两边摆在一起比, 才叫真的钉住了.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from conftest import BUYER_ID, TOKEN

from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.db.entities import Message
from CharAgent.stream import EventType, StreamEvent
from CharAgent.tests.doubles import FakeRecordDatabase
from CharAgent.tests.mock_llm import (
    MockLLM,
    make_tool_call,
    text_response,
    tool_call_response,
)
from CharApp.minimall import service as service_module
from CharApp.minimall.client import HEADER_TOKEN, HEADER_USER_ID
from CharApp.minimall.config import (
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
    ServerConfig,
)
from CharApp.minimall.knowledge.citations import (
    CITATION_SNIPPET_LENGTH,
    Citations,
    citation_sink,
    citations_from_results,
    citations_from_text,
)
from CharApp.minimall.server import create_minimall_app
from CharApp.minimall.service import MinimallService

APP_HOST = "http://charapp"

# 这段玩具对话的编号 (与买家 ID 无关, 只是个稳定值)
THREAD = "minimall:10:web"

# 一次检索的返回文本 (形状由 `formatting.format_chunks` 定死) —— 下面几条用例共用
RESULT = (
    "[1] 退款政策: 付款之后怎么退钱\n"
    "## 什么情况可以申请退款\n\n订单付款之后都可以提交退款申请.\n"
    "\n"
    "[2] 运费与配送说明\n"
    "## 运费\n\n全站免运费.\n"
)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class StubRetriever:
    """检索器替身: 按预置的片段应答 (每次调用记一笔, 便于断言编号)."""

    def __init__(self, chunks: list[dict] | None = None) -> None:
        self._chunks = chunks if chunks is not None else DEFAULT_CHUNKS
        self.queries: list[str] = []

    async def search(self, query: str, *, top_k: int | None = None) -> list[dict]:
        self.queries.append(query)
        return list(self._chunks)


# 一段只在**摘录之外**的正文 (被 200 字那一刀切掉的部分): 否定断言拿它证明
# "进浏览器的只是截断后的摘录", 而不是整段工具返回.
LONG_TAIL = "填" * 260 + "这一句在摘录之外, 不该出现在浏览器里"

DEFAULT_CHUNKS = [
    {
        "id": "refund-policy-0",
        "slug": "refund-policy",
        "title": "退款政策: 付款之后怎么退钱",
        "category": "policy",
        "chunk_index": 0,
        "content": (
            "## 什么情况可以申请退款\n\n订单付款之后都可以提交退款申请.\n\n" + LONG_TAIL
        ),
        "score": 0.9,
    },
    {
        "id": "shipping-and-delivery-0",
        "slug": "shipping-and-delivery",
        "title": "运费与配送说明",
        "category": "shipping",
        "chunk_index": 0,
        "content": "## 运费\n\n全站免运费.",
        "score": 0.7,
    },
]


# ---------------------------------------------------------------------------
# 1. 解析与编号 (纯函数)
# ---------------------------------------------------------------------------


def test_a_result_text_turns_into_numbered_citations() -> None:
    """`[n]` + 标题 + 正文 → 标题与**服务端截的**摘录 (200 字).

    摘录取自检索回来的原文 (不是模型的话): 卡片上那句"答的是哪一段"必须与语料
    对得上, 模型复述过什么不作数.
    """
    citations = citations_from_text(RESULT)

    assert [c["n"] for c in citations] == [1, 2]
    assert citations[0]["title"] == "退款政策: 付款之后怎么退钱"
    assert citations[0]["snippet"].startswith("## 什么情况可以申请退款")
    assert citations[1]["title"] == "运费与配送说明"
    assert "slug" not in citations[0], "这段文本里没有 slug, 也不该凭空造一个"


def test_an_over_long_body_is_cut_to_the_snippet_length() -> None:
    long_body = "正" * 500

    citations = citations_from_text(f"[1] 标题\n{long_body}")

    assert len(citations[0]["snippet"]) == CITATION_SNIPPET_LENGTH


def test_a_number_inside_the_body_is_not_a_new_block() -> None:
    """正文里出现一行 `[7] …` 不算新的一段 —— 编号**必须连着**才认.

    政策原文里写个编号 (或者 markdown 里那类方括号) 是可能的, 而它一旦被当成段落
    边界, 摘录就会从中间截断. 判据是"下一个号", 不是"看着像".
    """
    text = "[3] 退款政策\n正文第一行\n[7] 这段正文自己写了编号\n正文继续\n"

    citations = citations_from_text(text)

    assert [c["n"] for c in citations] == [3]
    assert "[7] 这段正文自己写了编号" in citations[0]["snippet"]


def test_the_ledger_hands_out_consecutive_numbers() -> None:
    """一次检索领几个号由工具报, 下一次接着数 (同一段对话里编号唯一)."""
    citations = Citations()

    assert citations.take(2) == 1
    assert citations.take(3) == 3
    assert citations.take(0) == 6, "空的一批不占号"


def test_seeding_an_old_conversation_continues_the_numbering() -> None:
    """恢复旧会话: 旧的检索结果垫在前面, 编号从它后面接着数.

    不这么做的话, 进程重启后接着聊的那一段会从 `[1]` 重数 —— 同一段对话里两个
    `[1]`, 引用就回指不明白了 (ADR-0027 的「代价与边界」里记着这条).
    """
    citations = Citations([RESULT])

    assert citations.take(2) == 3, "旧会话里已经有 1 与 2 了"
    assert citations.take(1) == 5, "接着往下数, 不回卷"
    assert [c["n"] for c in citations.all()] == [1, 2], "旧的那两条仍在账上"


def test_a_duplicated_number_keeps_the_first_one() -> None:
    """编号撞车时留先出现的那一条 (编号没连续累加的会话才可能撞)."""
    merged = citations_from_results([RESULT, RESULT])

    assert [c["n"] for c in merged] == [1, 2]


# ---------------------------------------------------------------------------
# 2. 出口: 只有 final 事件被补
# ---------------------------------------------------------------------------


async def test_the_sink_attaches_only_the_cited_sources_to_the_final_event() -> None:
    """`final` 挂**这条答复引用到的那几段**, 其余事件原样转交 (载荷一个键都不动).

    两个判据合在一条里:
    - **按引用过滤**: 账上有 1 与 2, 而答复只写了 `[1]` → 只带 1 (ADR-0027 的边界是
      "被引用的那一段才进浏览器", 不是"检索到的那几段都进").
    - `approval_required` 那类终局事件**没有**答复正文, 也就不该带引用 —— 补上去
      只会让前端以为"这次没答完的回答里也有引用".
    """
    collected: list[EventSinkEvent] = []

    async def inner(event: StreamEvent) -> None:
        collected.append(EventSinkEvent(event.type, dict(event.data)))

    citations = Citations([RESULT])
    sink = citation_sink(inner, citations)
    final = StreamEvent(EventType.FINAL, 3, {"content": "答: 可以退 [1]"})
    both = StreamEvent(EventType.FINAL, 4, {"content": "可以退 [1], 运费也免 [2]"})
    tool_call = StreamEvent(EventType.TOOL_CALL, 1, {"tool_name": "search_knowledge"})
    approval = StreamEvent(
        EventType.APPROVAL_REQUIRED, 3, {"tool_name": "pay_my_order"}
    )

    await sink(tool_call)
    await sink(final)
    await sink(both)
    await sink(approval)

    assert collected[0].data == {"tool_name": "search_knowledge"}
    assert [c["n"] for c in collected[1].data["citations"]] == [1], "没引用的不该带上"
    assert [c["n"] for c in collected[2].data["citations"]] == [1, 2]
    assert final.data["citations"] == collected[1].data["citations"], "应当是原地补键"
    assert "citations" not in collected[3].data


class EventSinkEvent:
    """出口收到的那一份快照 (类型 + 载荷的浅拷贝)."""

    def __init__(self, type_: EventType, data: dict[str, Any]) -> None:
        self.type = type_
        self.data = data


# ---------------------------------------------------------------------------
# 3. 端到端: 直播那份引用与 /history 那份逐字一致
# ---------------------------------------------------------------------------

# 模型的两轮: 先查知识库, 再照着查到的内容答 (答里带 [1] / [2])
SCRIPT = [
    tool_call_response(make_tool_call("search_knowledge", '{"query": "退款 运费"}')),
    text_response("可以退 [1]."),
]


def allow_app(mall: respx.MockRouter) -> None:
    """放行打向本服务的请求 (respx 只该拦假商城那几条)."""
    mall.route(host="charapp").pass_through()


@contextlib.asynccontextmanager
async def talking_to(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """直接打 app 的客户端 (ASGI 传输: 不起服务, 不占端口)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=APP_HOST
    ) as client:
        yield client


def headers() -> dict[str, str]:
    return {HEADER_TOKEN: TOKEN, HEADER_USER_ID: str(BUYER_ID)}


def parse_sse(body: str) -> list[dict[str, Any]]:
    """SSE 正文 → 逐事件 (与 `test_server.py` 同一份读法)."""
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


def final_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    """这条流的 final 事件 (恰好一个)."""
    finals = [event for event in events if event["event"] == "final"]
    assert len(finals) == 1, f"final 应当恰好一个: {[e['event'] for e in events]}"
    return finals[0]


@pytest.fixture
def stub_retriever(monkeypatch) -> StubRetriever:
    """把装配处现造的那个检索器换成替身 (界面不动: 仍是每条会话一个)."""
    stub = StubRetriever()
    monkeypatch.setattr(
        service_module,
        "KnowledgeRetriever",
        lambda config, *, model=None: stub,
    )
    return stub


def serving(records: FakeRecordDatabase) -> Any:
    """起一个客服服务 (假商城客户端 + 假模型 + 假记录库)."""
    service = MinimallService(
        client=_mall_client(),
        model=MockLLM.scripted(SCRIPT),
        saver=InMemoryCheckpointSaver(),
        database=records,
    )
    return create_minimall_app(
        service,
        ServerConfig(host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT, token=TOKEN),
    )


def _mall_client():
    """接到假商城上的客户端 (respx 拦着, 不触网)."""
    from conftest import AGENT_BASE_URL

    from CharApp.minimall.client import MinimallClient

    return MinimallClient(base_url=AGENT_BASE_URL, token=TOKEN)


async def test_the_final_event_and_the_history_carry_the_same_citations(
    mall, stub_retriever
) -> None:
    """问一句 → 事件流里的 `final` 带引用 → `/history` 回读出**逐字一样**的引用.

    这条用例钉的是这一片的立身之本: 直播与刷新走的是两条代码路径 (一个从钩子收
    原料, 一个从记录表回读), 而买家看到的必须是同一份. 两边都比的是**整份列表**,
    不只是"有几条" —— 号码错位或摘要长短不一都会当场红.
    """
    allow_app(mall)
    records = FakeRecordDatabase()
    app = serving(records)

    async with talking_to(app) as http:
        asked = await http.post(
            "/runs", json={"message": "退款怎么弄"}, headers=headers()
        )
        live = final_event(parse_sse(asked.text))["data"]["citations"]
        history = await http.get("/history", headers=headers())

    messages = history.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    kept = messages[1]["citations"]

    assert [c["n"] for c in live] == [1], "答复只引了 [1] —— 载荷里不该有第二条"
    assert live[0]["title"] == "退款政策: 付款之后怎么退钱"
    assert kept == live, "刷新之后看到的那一份必须与直播那次逐字一致"
    assert stub_retriever.queries == ["退款 运费"], "检索词是模型填的那个"


async def test_a_run_without_knowledge_has_an_empty_citation_list(mall) -> None:
    """没查过知识库的一次运行: `citations` 是**空列表**, 不是缺字段.

    前端因此不必分辨"没有这个字段"与"字段是空的" —— 两条路 (直播 / 历史) 都给
    同一个形状.
    """
    allow_app(mall)
    app = create_minimall_app(
        MinimallService(
            client=_mall_client(),
            model=MockLLM.fixed(text_response("好的")),
            saver=InMemoryCheckpointSaver(),
        ),
        ServerConfig(host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT, token=TOKEN),
    )

    async with talking_to(app) as http:
        asked = await http.post("/runs", json={"message": "你好"}, headers=headers())

    assert final_event(parse_sse(asked.text))["data"]["citations"] == []


async def test_the_tool_payload_never_reaches_the_browser_with_citations(
    mall, stub_retriever
) -> None:
    """**否定断言** (ADR-0003 的主规则没被破坏): 响应里搜不到工具参数与完整返回.

    引用开的口子只放行「被引用的那一段」—— 检索词 (`arguments`) 与整段工具返回
    (含没有进引用的那些段落) 仍然不出本进程. 判据取两个只可能来自工具原文的串:
    模型填的检索词, 以及**没有被引用**的那一段正文.
    """
    allow_app(mall)
    app = serving(FakeRecordDatabase())

    async with talking_to(app) as http:
        asked = await http.post(
            "/runs", json={"message": "退款怎么弄"}, headers=headers()
        )
        history = await http.get("/history", headers=headers())

    body = asked.text + history.text
    assert "退款 运费" not in body, "工具的参数原文进了浏览器"
    assert "这一句在摘录之外" not in body, (
        "摘录之外的那一截也进了浏览器 —— 进引用的只该是截断后的前 200 字"
    )
    # 没被引用的那一段**整段**都不该在 (答复只引了 [1]; [2] 是检索到但没引用的)
    assert "运费与配送说明" not in body, "检索到而没被引用的段落进了浏览器"
    # 反面对照: 被引用的那一段本身**该**在 (否则上面几条断言可以被"什么都不发"骗过)
    assert "订单付款之后都可以提交退款申请" in body


# ---------------------------------------------------------------------------
# 4. 历史那半边: 记录行 → 每条消息该带的引用
# ---------------------------------------------------------------------------


def _message(role: str, content: str, *, run_id: str | None) -> Message:
    """一行记录 (带 run_id —— `record_message` 那个工厂写死 None, 这里要能指名)."""
    return Message(
        message_id=uuid4().hex,
        thread_id=THREAD,
        run_id=run_id,
        role=role,
        content=content,
        reasoning=None,
        tool_call_ids=[],
        hidden=False,
        created_at=datetime.now(UTC),
    )


async def test_history_extras_only_touch_assistant_rows_with_a_run() -> None:
    """只有 assistant 行、且有 run 的行才补引用 (用户自己打的 `[n]` 不该可点)."""
    from CharApp.minimall.history_citations import HistoryCitations

    records = FakeRecordDatabase(
        messages=[
            _message("user", "退款怎么弄", run_id="run-1"),
            _message("assistant", "可以退 [1], 运费也免 [2]", run_id="run-1"),
        ],
        tool_calls=[_search_call("run-1", RESULT)],
    )

    extras = await HistoryCitations(records).provide(THREAD, records.messages)

    assert list(extras) == [1], "只给 assistant 那一行"
    assert [c["n"] for c in extras[1]["citations"]] == [1, 2]


def _search_call(run_id: str, result: str):
    """一行工具调用 (检索成功那种) —— 用框架的实体构造, 形状与落库的一致."""
    from CharAgent.db.entities import ToolCall, ToolCallStatus

    return ToolCall(
        run_id=run_id,
        message_id=f"{run_id}:1",
        tool_call_id="call_search_knowledge",
        tool_name="search_knowledge",
        arguments='{"query": "退款"}',
        status=ToolCallStatus.SUCCEEDED,
        result=result,
    )
