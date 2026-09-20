"""工具提供者契约: 给定买家, 交出正好 9 个工具, 而且身份进不了它们的参数表.

本文件的重点只有一个 —— **那个安全守卫** (PRD §4.2). 它是那条核心设计的可执行
证据: 模型的视角就是工具的 JSON schema (名字 + 说明 + 参数表), 里面没有的东西
它看不见, 也就无从填别人的值。这条不能靠「我记得」, 得靠遍历断言 ——
所以下面不止断言「没有 user_id」, 而是把 9 个工具的参数表**逐个钉死**.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import BUYER_ID, TOOL_NAMES, agent_url

from CharAgent.agent import RunContext
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import MinimallConfigError
from CharApp.minimall.provider import (
    PAYLOAD_USER_ID,
    MinimallToolProvider,
    buyer_id,
)

# 每个工具**应该**有的参数 (一个不多一个不少) —— 身份一旦混进来, 这里立刻红
EXPECTED_PARAMS: dict[str, set[str]] = {
    "search_products": {
        "keyword",
        "category",
        "min_price",
        "max_price",
        "ordering",
        "page",
        "page_size",
    },
    "get_product_detail": {"slug"},
    "list_categories": set(),
    "list_featured_products": set(),
    "get_my_cart": set(),
    "list_my_orders": {"page", "page_size"},
    "get_my_order": {"order_no"},
    "get_my_profile": set(),
    "list_my_addresses": set(),
}

# 任何形态的身份字段都不许出现在参数表里 (中英文写法都列上)
IDENTITY_KEYS = {"user_id", "user", "buyer_id", "buyer", "uid", "买家"}

# 守卫用的买家 ID: 一个在别处不会出现的怪数字 —— 「身份有没有漏进 schema」这条
# 断言靠的就是它在文本里搜不到, 撞上别的数字会让守卫假红
GUARD_BUYER_ID = 987654


def context_for(
    user_id: Any = BUYER_ID, *, thread: str = "minimall:3:cli"
) -> RunContext:
    """造一个运行上下文 (与 `service.build_context` 造的是同一个形状)."""
    return RunContext(thread_id=thread, payload={PAYLOAD_USER_ID: user_id})


async def test_a_provider_returns_exactly_nine_tools(client: MinimallClient) -> None:
    """给定买家, 交出正好 9 个工具, 名字与顺序都对."""
    tools = await MinimallToolProvider(client).provide(context_for())

    assert tuple(item.name for item in tools) == TOOL_NAMES


async def test_a_provider_is_anything_with_the_right_shape(
    client: MinimallClient,
) -> None:
    """提供者是结构化协议: 有那个方法就算, 不继承任何基类 (与 ChatModel 同款).

    这条不是重复上面的断言, 而是钉住「业务不 import 框架基类」这件事 ——
    框架与业务之间只有形状约定, 没有继承关系.
    """
    provider = MinimallToolProvider(client)

    assert type(provider).__mro__ == (MinimallToolProvider, object), (
        "提供者不该继承框架的基类: 协议是结构化的, 有 provide 就算"
    )
    assert await provider.provide(context_for()) is not None


# ---------------------------------------------------------------------------
# 守卫: 身份不进参数表
# ---------------------------------------------------------------------------


async def test_identity_never_appears_in_any_tool_schema(
    client: MinimallClient,
) -> None:
    """**本文件的核心**: 9 个工具的参数表里没有身份, 一个都没有.

    做法是两层: 先逐个钉死每个工具**应该**有哪些参数 (多一个都不行), 再把整份
    schema 序列化成文本搜一遍买家 ID —— 后者兜住「参数名不叫 user_id 但值漏了」
    这类形态.
    """
    tools = await MinimallToolProvider(client).provide(context_for(GUARD_BUYER_ID))

    actual = {item.name: set(item.parameters.get("properties", {})) for item in tools}
    assert actual == EXPECTED_PARAMS

    for item in tools:
        schema = json.dumps(
            {
                "name": item.name,
                "description": item.description,
                "parameters": item.parameters,
            },
            ensure_ascii=False,
        )
        leaked = IDENTITY_KEYS & set(item.parameters.get("properties", {}))
        assert leaked == set(), f"{item.name} 的参数表里有身份字段: {leaked}"
        assert str(GUARD_BUYER_ID) not in schema, (
            f"{item.name} 的 schema 里漏了买家 ID: {schema}"
        )


async def test_the_identity_only_tools_take_no_arguments_at_all(
    client: MinimallClient,
) -> None:
    """「查我自己的东西」那类工具一个参数都不收 —— 模型连可以填错的空都没有.

    顺带钉住 `required`: 空参数表不该声明任何必填项 (否则模型会收到「缺少必填
    参数」的怪错).
    """
    tools = await MinimallToolProvider(client).provide(context_for())
    no_argument = [item for item in tools if EXPECTED_PARAMS[item.name] == set()]

    assert len(no_argument) == 5, "空参数工具应当有 5 个 (分类/精选/购物车/余额/地址)"
    for item in no_argument:
        assert item.parameters.get("required", []) == []


# ---------------------------------------------------------------------------
# 身份: 从上下文到请求, 一路都在
# ---------------------------------------------------------------------------


async def test_the_context_identity_reaches_the_request(
    client: MinimallClient, mall
) -> None:
    """上下文里的买家 ID 一路走到请求头 —— 提供者不是收下就扔了.

    走的是完整的装配路径 (`provide(context)` → 工具 → 客户端 → HTTP), 不是
    直接调 `build_tools` —— 后者只证明工具装得对, 证明不了上下文那条线也通.
    """
    route = mall.get(agent_url("profile/")).mock(
        return_value=httpx.Response(200, json={})
    )
    tools = await MinimallToolProvider(client).provide(context_for(9921))
    profile = next(item for item in tools if item.name == "get_my_profile")

    await profile.fn()

    assert route.calls[0].request.headers["X-User-Id"] == "9921"


async def test_a_context_without_an_identity_fails_loudly(
    client: MinimallClient,
) -> None:
    """载荷里没有买家身份时当场报错, 而不是悄悄查出一个空结果.

    这是**装配代码**的错误 (忘了往 payload 里放身份). 报错信息要指得出这一点 ——
    否则它会伪装成「商城没数据」, 排查时从最远的地方开始找.
    """
    provider = MinimallToolProvider(client)
    context = RunContext(thread_id="minimall:?:cli", payload={})

    with pytest.raises(MinimallConfigError) as excinfo:
        await provider.provide(context)

    assert PAYLOAD_USER_ID in str(excinfo.value)


@pytest.mark.parametrize("bad", ["三号买家", None, "", {"id": 3}])
async def test_a_non_integer_identity_is_rejected(
    client: MinimallClient, bad: Any
) -> None:
    """身份必须是整数 —— 否则它会以 `X-User-Id: 三号买家` 的形式打到商城去吃 400.

    在装配期就拦住: 那时还说得清是谁填错了.
    """
    with pytest.raises(MinimallConfigError):
        buyer_id(context_for(bad))
