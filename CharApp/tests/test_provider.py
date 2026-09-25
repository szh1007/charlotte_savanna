"""工具提供者契约: 给定买家, 交出正好 18 个工具, 而且身份与密码都进不了参数表.

本文件的重点只有一个 —— **那个安全守卫** (PRD §4.2). 它是那条核心设计的可执行
证据: 模型的视角就是工具的 JSON schema (名字 + 说明 + 参数表), 里面没有的东西
它看不见, 也就无从填别人的值。这条不能靠「我记得」, 得靠遍历断言 ——
所以下面不止断言「没有 user_id」, 而是把 18 个工具的参数表**逐个钉死**.

L2 加了 8 个**写**工具之后这条守卫更值钱了: 写工具能改数据, 身份漏进它们的参数表
就不只是「看得到别人的数据」, 而是「改得动别人的数据」—— 所以 EXPECTED_PARAMS 里
那 8 行是逐个键写死的, 多一个 `user_id` 就红.

**L3 的守卫有第二个维度** (issue 35, ADR-0015): 代付的支付密码走的是同一条路
(`RunContext.payload` → 闭包), 而它要防的是另一件事 —— 密码一旦成了参数, 模型就
会自己编一个填进去, 而编出来的值会走 `arguments` 落库. 所以身份那条断言照旧成立
之外, 还有一条**同一个判据**的断言: schema 里搜不到密码 (连它带的值也搜不到).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import BUYER_ID, ORDER_NO, PAYMENT_PASSWORD, TOOL_NAMES, agent_url

from CharAgent.agent import RunContext
from CharAgent.tests.mock_llm import make_tool_call
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import MinimallConfigError
from CharApp.minimall.guardrail import WriteGuardrail
from CharApp.minimall.provider import (
    PAYLOAD_USER_ID,
    MinimallToolProvider,
    buyer_id,
    one_shot_payload,
)
from CharApp.minimall.tools import ONE_SHOT_FIELDS, PAYMENT_PASSWORD_FIELD

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
    # --- 8 个写工具 (L2) ---
    "add_to_cart": {"slug", "quantity"},
    "update_cart_item": {"slug", "quantity"},
    "remove_cart_item": {"slug"},
    "clear_cart": set(),
    "place_order": {"address_id"},
    "cancel_my_order": {"order_no"},
    "request_refund": {"order_no"},
    "list_my_refunds": set(),
    # --- 代付 (L3b, issue 35) ---
    "pay_my_order": {"order_no"},
}

# 任何形态的身份字段都不许出现在参数表里 (中英文写法都列上)
IDENTITY_KEYS = {"user_id", "user", "buyer_id", "buyer", "uid", "买家"}

# 代付的支付密码在 schema 里可能的写法 —— 与身份那条同一个判据, 换个维度
PASSWORD_KEYS = {"payment_password", "password", "pay_password", "支付密码"}

# 守卫用的买家 ID: 一个在别处不会出现的怪数字 —— 「身份有没有漏进 schema」这条
# 断言靠的就是它在文本里搜不到, 撞上别的数字会让守卫假红
GUARD_BUYER_ID = 987654

# 守卫用的支付密码 (同一条思路: 一个在别处不会出现的怪值)
GUARD_PASSWORD = PAYMENT_PASSWORD


def context_for(
    user_id: Any = BUYER_ID,
    *,
    thread: str = "minimall:3:cli",
    payload: dict[str, Any] | None = None,
) -> RunContext:
    """造一个运行上下文 (与 `service.build_context` 造的是同一个形状).

    Args:
        user_id: 载荷里的买家身份.
        thread: 会话编号 (断言分隔用的自由字段, 与身份无关).
        payload: 另外并进载荷的东西 (代付的判据要一份带密码的上下文).
    """
    return RunContext(
        thread_id=thread,
        tenant_id="minimall",
        user_id=str(user_id),
        payload={PAYLOAD_USER_ID: user_id, **(payload or {})},
    )


async def test_a_provider_returns_exactly_eighteen_tools(
    client: MinimallClient,
) -> None:
    """给定买家, 交出正好 18 个工具, 名字与顺序都对."""
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


async def _assert_nothing_leaks(
    client: MinimallClient, context: RunContext, *, keys: set[str], value: str
) -> None:
    """遍历每个工具的 schema, 断言里面搜不到那批键名, 也搜不到那个值.

    两层都要 (下面两条守卫共用它): 键名那一层是"参数表里没有这个名字", 文本那一
    层兜住「名字对不上但值漏了」(说明里带一句 / 默认值里塞一个) —— 而模型的视角
    正是这段文本.
    """
    tools = await MinimallToolProvider(client).provide(context)

    for item in tools:
        schema = json.dumps(
            {
                "name": item.name,
                "description": item.description,
                "parameters": item.parameters,
            },
            ensure_ascii=False,
        )
        leaked = keys & set(item.parameters.get("properties", {}))
        assert leaked == set(), f"{item.name} 的参数表里有禁区字段: {leaked}"
        assert value not in schema, f"{item.name} 的 schema 里漏了 {value!r}: {schema}"


async def test_identity_never_appears_in_any_tool_schema(
    client: MinimallClient,
) -> None:
    """**本文件的核心**: 18 个工具的参数表里没有身份, 一个都没有.

    做法是两层: 先逐个钉死每个工具**应该**有哪些参数 (多一个都不行), 再把整份
    schema 序列化成文本搜一遍买家 ID —— 后者兜住「参数名不叫 user_id 但值漏了」
    这类形态.
    """
    tools = await MinimallToolProvider(client).provide(context_for(GUARD_BUYER_ID))

    actual = {item.name: set(item.parameters.get("properties", {})) for item in tools}
    assert actual == EXPECTED_PARAMS

    await _assert_nothing_leaks(
        client,
        context_for(GUARD_BUYER_ID),
        keys=IDENTITY_KEYS,
        value=str(GUARD_BUYER_ID),
    )


async def test_the_payment_password_never_appears_in_any_tool_schema(
    client: MinimallClient,
) -> None:
    """**同一个判据的第二个维度** (ADR-0015 的前提): schema 里没有密码.

    与身份那条一字不差的做法, 但这一条防的是**另一件事**: 身份漏了是"模型能填
    别人的值"; 密码漏了是"模型会编一个自己的值" —— 而被编出来的值会走
    `arguments` 落库 (三个"永不"里最容易被破的那一个).

    判据取「带着密码装出来的那批工具」: 载荷里**真的**有密码时它更不该露 —— 现值
    与键名一起搜 (只搜键名的话, 一个改名叫 `pwd` 的参数就混过去了).
    """
    context = context_for(
        GUARD_BUYER_ID, payload={PAYMENT_PASSWORD_FIELD: GUARD_PASSWORD}
    )

    await _assert_nothing_leaks(
        client, context, keys=PASSWORD_KEYS, value=GUARD_PASSWORD
    )


async def test_the_one_shot_payload_reaches_the_tool_but_not_the_schema(
    client: MinimallClient, mall
) -> None:
    """一次性载荷从**运行上下文**一路走到工具的闭包 (ADR-0015 的那条通路).

    走的是完整的装配路径 (`provide(context)` → 工具 → 客户端 → HTTP): 密码从
    payload 里被挑出来、进闭包、进请求体 —— 这一步是"密码到得了"的正证据, 上面
    两条否定断言才有意义 (一个到不了工具手里的密码, 当然也进不了 schema).

    顺带钉住的还有**载荷里的其他键**: 它们不进闭包 (见 `one_shot_payload`).
    """
    route = mall.request("POST", agent_url(f"orders/{ORDER_NO}/pay/")).mock(
        return_value=httpx.Response(200, json={"status": "paid"})
    )
    context = context_for(
        payload={PAYMENT_PASSWORD_FIELD: GUARD_PASSWORD, "language": "zh"}
    )

    tools = await MinimallToolProvider(client).provide(context)
    pay = next(item for item in tools if item.name == "pay_my_order")
    await pay.fn(order_no=ORDER_NO)

    assert json.loads(route.calls[0].request.content) == {
        PAYMENT_PASSWORD_FIELD: GUARD_PASSWORD
    }


async def test_the_credential_list_matches_what_the_guardrail_asks_for(
    client: MinimallClient,
) -> None:
    """装配时挑的那批键名, 与护栏声明要什么 (`needs`) 是**同一批** —— 一条守两处.

    这两处一旦漂开, 症状极难查: 护栏按新名字要数据, 而装配只认识老名字, 于是工具
    拿到空闭包、回一句「没有拿到授权」—— 用户明明输了密码, 系统却说没收到.

    所以这条**真的去问一次护栏** (拿它自己那条裁决的 `needs` 比对), 而不是在同一个
    模块里自证: 自证只能证明「常量等于它自己」, 护栏那边换个字面量它照样绿.
    """
    tools = await MinimallToolProvider(client).provide(context_for())
    pay = next(item for item in tools if item.name == "pay_my_order")
    guardrail = WriteGuardrail(client=client, user_id=BUYER_ID)

    decision = await guardrail(turn=1, call=make_tool_call("pay_my_order"), tool=pay)

    assert decision is not None and decision.needs == ONE_SHOT_FIELDS
    # 身份走的是另一条路 (载荷里的 `user_id` 由 buyer_id 取), 别混进凭据清单
    assert PAYLOAD_USER_ID not in ONE_SHOT_FIELDS


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {PAYMENT_PASSWORD_FIELD: None},
        {PAYMENT_PASSWORD_FIELD: ""},
        {PAYMENT_PASSWORD_FIELD: 0},
    ],
)
def test_a_missing_or_empty_credential_is_not_a_credential(payload: dict) -> None:
    """缺 / None / 空串一律不算"拿到了" —— 它们换来的是一次空密码撞端点.

    `0` 也在里面: 它是个正经的"有值", 但对密码来说是**假的** (测试用的密码是
    六位数字串). 这一条只钉装配那一层的口径, 工具那一层的守卫在 `test_tools.py`.
    """
    context = context_for(payload=payload)

    assert one_shot_payload(context) == {}


async def test_the_identity_only_tools_take_no_arguments_at_all(
    client: MinimallClient,
) -> None:
    """「查我自己的东西」那类工具一个参数都不收 —— 模型连可以填错的空都没有.

    顺带钉住 `required`: 空参数表不该声明任何必填项 (否则模型会收到「缺少必填
    参数」的怪错).
    """
    tools = await MinimallToolProvider(client).provide(context_for())
    no_argument = [item for item in tools if EXPECTED_PARAMS[item.name] == set()]

    # 5 个只读 (分类/精选/购物车/余额/地址) + 2 个写 (清空购物车/看我的退款)
    assert len(no_argument) == 7
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
    context = RunContext(
        thread_id="minimall:?:cli", tenant_id="minimall", user_id="?", payload={}
    )

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
