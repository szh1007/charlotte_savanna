"""会话记录与水合 (ticket 17) 在**业务这一侧**的样子.

框架那两件事 (`db/recorder.py` 的记账、`client/session.py` 的水合) 自己有专门的
用例; 这一页断的是**业务接上了没有**:

1. **两个入口都记账** —— 装配只有一处 (`MinimallService.session_for`), 命令行与
   服务进程都经过它, 于是「网页版记了、命令行没记」这种半边生效不该发生.
2. **重启之后接得上上文** —— 换一个服务对象、同一个快照后端与会话编号, 第二段
   进程里的模型看得到上一段聊过什么 (L2.5 那条验收在业务侧的机制).
3. **两个入口是两个租户** —— 同一买家在网页端与命令行聊出来的会话分开放, 于是
   前端左栏不会混进命令行那些调试痕迹.

模型与商城全走替身 (MockLLM + 内存快照 + respx 假商城), 离线可跑.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from conftest import AGENT_BASE_URL, BUYER_ID, TOKEN

from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.db.errors import PricingNotReadyError
from CharAgent.tests.doubles import FakeRecordDatabase
from CharAgent.tests.mock_llm import MockLLM, text_response
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.service import (
    TENANT_CLI,
    TENANT_WEB,
    MinimallService,
    build_context,
)


@pytest.fixture
async def mall_client() -> AsyncIterator[MinimallClient]:
    """一个接到假商城上的客户端 (本页的模型不调工具, 所以它基本只是装配的零件)."""
    instance = MinimallClient(base_url=AGENT_BASE_URL, token=TOKEN)
    yield instance
    await instance.aclose()


def service_for(
    client: MinimallClient, model: Any, saver: Any, database: Any = None
) -> MinimallService:
    """按生产那条线装一份零件 (差别只在快照与记录表是替身)."""
    return MinimallService(client=client, model=model, saver=saver, database=database)


async def test_both_entries_record_through_the_same_assembly(
    mall_client: MinimallClient,
) -> None:
    """网页端与命令行各问一句: 两边的账都进了记录表, 且各归各的属主.

    记录员是在**唯一一处装配**里绑上属主的 (见 `session_for`), 于是这一条同时钉住
    两件事: 记账真的接上了, 以及租户分得开.
    """
    records = FakeRecordDatabase()
    service = service_for(
        mall_client,
        MockLLM.fixed(text_response("好的")),
        InMemoryCheckpointSaver(),
        records,
    )

    for tenant, conversation in ((TENANT_WEB, "web"), (TENANT_CLI, "cli")):
        context = build_context(BUYER_ID, conversation, tenant_id=tenant)
        session = await service.session_for(
            context, event_sink=lambda event: None, redact=False
        )
        await session.ask("订单到哪了")

    thread_rows = records.rows_of("charagent_threads")
    assert {row["tenant_id"] for row in thread_rows} == {TENANT_WEB, TENANT_CLI}
    assert {row["user_id"] for row in thread_rows} == {str(BUYER_ID)}, (
        "属主是买家 ID 的字符串形态 (框架只把它当过滤键)"
    )
    assert len(records.rows_of("charagent_runs")) == 2
    written = [row["content"] for row in records.rows_of("charagent_messages")]
    assert written.count("订单到哪了") == 2, "两边各记了一句提问"
    assert written.count("好的") == 2, "也各记了一句答复"


async def test_a_restarted_service_picks_up_the_previous_conversation(
    mall_client: MinimallClient,
) -> None:
    """重启 (换一个服务对象) 之后, 第二个进程里的模型看得到上一段聊过什么.

    L2.5 那条验收 («重启服务 → 前端历史还在且模型接着上文答») 在业务侧的机制:
    两个服务共用同一个快照后端与会话编号, 而装配出来的会话第一次提问前会把历史
    读回来 —— 业务这一侧一行都不用写.
    """
    saver = InMemoryCheckpointSaver()
    context = build_context(BUYER_ID, "web", tenant_id=TENANT_WEB)
    first_session = await service_for(
        mall_client, MockLLM.fixed(text_response("订单已发货")), saver
    ).session_for(context, event_sink=lambda event: None, redact=False)
    await first_session.ask("订单到哪了")

    # 重启: 新服务、新模型, 同一个 saver 与同一个会话编号
    model = MockLLM.fixed(text_response("预计明天到"))
    restarted = await service_for(mall_client, model, saver).session_for(
        context, event_sink=lambda event: None, redact=False
    )
    await restarted.ask("那什么时候能到")

    seen = [str(message.get("content")) for message in model.calls[0]["messages"]]
    assert "订单到哪了" in seen, "上一段进程里的问题要读回来"
    assert "订单已发货" in seen, "上一段的答复也要读回来"
    assert seen[-1] == "那什么时候能到", "新问的那句接在最后"


def test_the_two_entries_are_two_tenants() -> None:
    """同一买家的网页端与命令行是**两个租户**, 会话编号却只有第三段不同.

    为什么要分开 (而不是都用 "minimall"): 命令行里敲的那些是开发者自己的调试痕迹,
    不该出现在买家的会话列表里 —— 分租户之后「列表按属主过滤」顺手就把这件事做了.

    载荷里仍然是**整数**买家 ID: 那是业务自己的口径 (工具闭包按它取身份), 与框架
    认的那两个字符串字段各走各的.
    """
    web = build_context(BUYER_ID, "web", tenant_id=TENANT_WEB)
    cli = build_context(BUYER_ID, "cli", tenant_id=TENANT_CLI)

    assert (web.tenant_id, cli.tenant_id) == ("minimall", "minimall-cli")
    assert web.user_id == cli.user_id == str(BUYER_ID)
    assert web.thread_id == f"minimall:{BUYER_ID}:web"
    assert cli.thread_id == f"minimall:{BUYER_ID}:cli"
    assert web.payload["user_id"] == BUYER_ID, "载荷里的身份仍是整数"


# ---------------------------------------------------------------------------
# 计价那条路的启动自检 (ticket 28)
# ---------------------------------------------------------------------------


def test_the_service_refuses_to_start_on_a_broken_price_table(
    mall_client: MinimallClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """价目表写错 -> **装配那一刻**就抛, 进程起不来.

    框架的记录员构造时也会验一次 (它才是花钱的那个角色); 这里验的是进程启动那一刻:
    两个入口共用 `MinimallService` 这一处装配, 所以命令行与服务进程都拦得住 ——
    与其等第一次收尾才发现「钱算不出来」, 不如一开始就不让它接活.
    """
    monkeypatch.setenv("CHARAGENT_MODEL_PRICES", '{"models": {"m": {"cache_miss": 1}}}')

    with pytest.raises(PricingNotReadyError) as info:
        service_for(
            mall_client,
            MockLLM([]),
            InMemoryCheckpointSaver(),
            database=FakeRecordDatabase(),
        )

    assert "output" in str(info.value), "报错要点名缺的是哪一档"


def test_the_service_starts_without_any_pricing_config(
    mall_client: MinimallClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没配价目表 -> 照常起 (不配价不是错误, 只是没有金额).

    这一条是「缺了也要能用」的验收: 演示环境不配价, 助手照样能问答.
    """
    monkeypatch.delenv("CHARAGENT_MODEL_PRICES", raising=False)

    service = service_for(
        mall_client,
        MockLLM([]),
        InMemoryCheckpointSaver(),
        database=FakeRecordDatabase(),
    )

    assert service is not None


def test_a_service_without_a_database_is_not_gated(
    mall_client: MinimallClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不记账的进程 (没有库) 不该被日历/配置拦住 —— 那笔钱它根本不记."""
    monkeypatch.setenv("CHARAGENT_MODEL_PRICES", "不是 JSON")

    service = service_for(mall_client, MockLLM([]), InMemoryCheckpointSaver())

    assert service is not None
