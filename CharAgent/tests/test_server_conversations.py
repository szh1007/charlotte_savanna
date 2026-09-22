"""会话列表端点 (ASGI 端到端): 「我聊过哪几段」 + 它只在配了库时存在.

三条主线:

1. **有路由**: 装配时给了库 → `GET /conversations` 在, 返回那批会话行 (投影成
   `conversation_id` / `title` / `updated_at` 三个字段).
2. **没有路由**: 没给库 → 404. 会话列表没有「内存里的版本」可言 (登记表里只有
   这个进程见过的那几段), 与其给一个会骗人的答案, 不如这条路由压根不存在 ——
   「不配不改行为」在这条路上意味着**既有 app 一个字都不变**.
3. **同一道门**: 认证失败 401; 写意图 405 (`Allow: GET`).

过滤与排序本身 (活跃状态 / 有可见消息 / 按最后活动倒序 / 租户与属主隔离) 是 SQL
的事, 由 `test_db_store.py` 的 pg_db 用例拿真库守 —— 这里给的是「库会返回哪些行」,
断的是路由与投影那一段接线.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from doubles import BrokenRecordDatabase, FakeRecordDatabase, record_thread
from mock_llm import MockLLM

from CharAgent.agent import RunContext
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.model.protocol import ChatModel
from CharAgent.server import (
    CONVERSATION_ID_FIELD,
    CONVERSATIONS_FIELD,
    CONVERSATIONS_PATH,
    LIMIT_QUERY,
    MESSAGES_FIELD,
    THREAD_ID_FIELD,
    TITLE_FIELD,
    UPDATED_AT_FIELD,
    ServerAuthError,
    create_app,
)

TOKEN = "toy-token"

# 两个固定时刻 (排序与格式都按它们断)
EARLY = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
LATE = datetime(2026, 9, 22, 15, 30, tzinfo=UTC)


class ToyContexts:
    """插座一: 认证 + 解析 (与 history / app 那两份同款, 只认两个头)."""

    async def provide(self, request: httpx.Request) -> RunContext:
        if request.headers.get("X-Toy-Token") != TOKEN:
            raise ServerAuthError()
        user = request.headers.get("X-Toy-User", "u-9f3a")
        return RunContext(
            thread_id=f"toy:{user}:1",
            tenant_id="toy",
            user_id=user,
            payload={"user": user},
        )


class ToySessions:
    """插座二: 装配 (会话列表这条路上它一次都不该被调到)."""

    def __init__(self, model: ChatModel) -> None:
        self._model = model
        self.built: list[ChatSession] = []

    async def provide(self, context: RunContext, *, event_sink) -> ChatSession:
        session = ChatSession(
            self._model,
            saver=InMemoryCheckpointSaver(),
            thread_id=context.thread_id,
            event_sink=event_sink,
        )
        self.built.append(session)
        return session


def build(records: FakeRecordDatabase | None) -> Any:
    """起一个玩具服务 (给了记录表就连它一起接上)."""
    return create_app(
        context_provider=ToyContexts(),
        session_provider=ToySessions(MockLLM.fixed(None)),  # type: ignore[arg-type]
        database=records,
    )


def headers(*, token: str | None = TOKEN) -> dict[str, str]:
    """玩具业务自己那套头 (框架一个都不认识)."""
    return {} if token is None else {"X-Toy-Token": token}


async def conversations(
    app: Any, *, method: str = "GET", query: str = "", **header_kwargs: Any
) -> httpx.Response:
    """拉一次会话列表 (默认 GET; 其余方法用来钉「只读」)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://toy"
    ) as http:
        return await http.request(
            method, f"{CONVERSATIONS_PATH}{query}", headers=headers(**header_kwargs)
        )


# ---------------------------------------------------------------------------
# 有路由: 返回什么
# ---------------------------------------------------------------------------


async def test_the_list_comes_back_projected_to_three_fields() -> None:
    """每条三个字段: 点进去要带的对话 ID / 列表上显示的标题 / 排序用的时刻.

    `conversation_id` 是会话编号的**第三段**: 请求头 `X-Conversation-Id` 收的就是
    它, 于是前端拿着列表项直接就能切回那段对话 (前两段说的是「这是谁的东西」,
    而列表本来就是按属主查的).
    """
    app = build(
        FakeRecordDatabase(
            threads=[
                record_thread("toy:u-9f3a:web", title="订单到哪了", updated_at=LATE),
                record_thread("toy:u-9f3a:cli", title="退换货", updated_at=EARLY),
            ]
        )
    )

    response = await conversations(app)

    assert response.status_code == 200
    assert response.json() == {
        CONVERSATIONS_FIELD: [
            {
                CONVERSATION_ID_FIELD: "web",
                TITLE_FIELD: "订单到哪了",
                UPDATED_AT_FIELD: LATE.isoformat(),
            },
            {
                CONVERSATION_ID_FIELD: "cli",
                TITLE_FIELD: "退换货",
                UPDATED_AT_FIELD: EARLY.isoformat(),
            },
        ]
    }


async def test_the_query_carries_the_caller_identity_and_the_limit() -> None:
    """查下去的条件是「这次请求认出来的属主」+ 这次要几条.

    判据只有两个, 而且都只能从认证插座来 (请求体与查询串影响不了它们) —— 这就是
    「看不到别人的会话」的全部实现. 这里断的是**传下去了**: 真按它过滤由 SQL 负责
    (`test_db_store.py` 的隔离用例拿真库守).
    """
    records = FakeRecordDatabase(threads=[record_thread("toy:u-9f3a:web")])
    app = build(records)

    await conversations(app, query=f"?{LIMIT_QUERY}=7")

    params = records.session.params
    assert params["tenant_id_1"] == "toy"
    assert params["user_id_1"] == "u-9f3a"
    assert 7 in params.values(), "限额要传下去 (仓储按它 LIMIT)"


async def test_without_a_limit_the_default_is_used() -> None:
    """不给 `limit` 用仓储那个默认上限 (不发明 offset, 也不返回全部)."""
    from CharAgent.db.repositories.threads import DEFAULT_LIST_LIMIT

    records = FakeRecordDatabase(threads=[record_thread("toy:u-9f3a:web")])
    app = build(records)

    await conversations(app)

    assert DEFAULT_LIST_LIMIT in records.session.params.values()


@pytest.mark.parametrize("bad", ["abc", "-1", "3.5"])
async def test_a_malformed_limit_is_refused(bad: str) -> None:
    """`limit` 不成形 → 400 (看不懂的请求当场说清, 不替它猜一个数)."""
    app = build(FakeRecordDatabase())

    response = await conversations(app, query=f"?{LIMIT_QUERY}={bad}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


async def test_zero_is_a_legal_limit() -> None:
    """`limit=0` 合法 (「一条都不要」), 回空列表 —— 不是 400."""
    app = build(FakeRecordDatabase(threads=[record_thread("toy:u-9f3a:web")]))

    response = await conversations(app, query=f"?{LIMIT_QUERY}=0")

    assert response.status_code == 200
    assert response.json() == {CONVERSATIONS_FIELD: []}


async def test_an_empty_list_is_not_a_missing_one() -> None:
    """一条会话都没有 → 空列表 (与 /history 同一条: 「还没有」不是错误)."""
    app = build(FakeRecordDatabase())

    response = await conversations(app)

    assert response.status_code == 200
    assert response.json() == {CONVERSATIONS_FIELD: []}


# ---------------------------------------------------------------------------
# 没有库: 这条路由压根不存在
# ---------------------------------------------------------------------------


async def test_the_route_is_not_registered_without_a_database() -> None:
    """没给库 → 404: 会话列表没有「内存里的版本」可答 (见模块 docstring).

    这是「不配不改行为」的落点: 既有调用方 (不配库的部署) 拿到的 app 与从前逐字
    一样 —— 多一个端点会改变它们的路由表, 那也是一种行为变化.
    """
    app = build(None)

    response = await conversations(app)

    assert response.status_code == 404


async def test_a_broken_record_store_answers_with_a_clean_503() -> None:
    """读不到记录表 → 503 + 机器可读的错误码 (与 /history 同一个信封)."""
    app = build(BrokenRecordDatabase())

    response = await conversations(app)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "record_store_unavailable"


async def test_the_other_routes_still_work_without_a_database() -> None:
    """没给库时另几条路照旧 (这条与上一条合起来才说明「只有列表那条是新的」)."""
    app = build(None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://toy"
    ) as http:
        history = await http.get("/history", headers=headers())

    assert history.status_code == 200
    assert history.json() == {THREAD_ID_FIELD: "toy:u-9f3a:1", MESSAGES_FIELD: []}


# ---------------------------------------------------------------------------
# 认证与只读
# ---------------------------------------------------------------------------


async def test_the_list_goes_through_the_same_door() -> None:
    """认证失败 → 401, 一条会话的标题也看不到 (与另三条逐字同款)."""
    app = build(
        FakeRecordDatabase(threads=[record_thread("toy:u-9f3a:web", title="秘密")])
    )

    response = await conversations(app, token=None)

    assert response.status_code == 401
    assert "秘密" not in response.text


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_writing_to_the_list_is_refused(method: str) -> None:
    """写意图一律 405 (`Allow: GET`) —— 列表是只读的, 没有「新建会话」这一说."""
    app = build(FakeRecordDatabase())

    response = await conversations(app, method=method)

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
