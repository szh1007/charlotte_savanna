"""17 个工具: 名字 / 参数契约 / 结果与两种失败的翻译.

工具是模型能对这个商城做的**全部**事情, 所以这里测的是它对外的那份契约:

- 名字与参数 (模型看到的就是这些)
- 结果: 商城返回什么, 模型就看到什么 (金额照原样, 不做 float 转换)
- 失败: 只读的 404 翻成人话 (「没有查到」), 写操作被商城按规则拒了也翻成人话
  (码 + 中文原话 + 一句下一步), 其余上抛给框架统一的内部错误文案

身份 (user_id) 的守卫不在这里, 在 `test_provider.py` —— 那是提供者契约的一部分;
护栏 (写预算 / 金额上限) 在 `test_guardrail.py`, 那是插件的事, 不是工具的.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import httpx
import pytest
from conftest import BUYER_ID, TOOL_NAMES, agent_url

from CharAgent.tool import Tool
from CharApp.minimall.client import MinimallClient, MinimallError
from CharApp.minimall.tools import build_tools


def tools_of(client: MinimallClient, user_id: int = BUYER_ID) -> dict[str, Tool]:
    """工具名 → 工具 (装配是纯函数, 每个用例现装一份, 互不干扰)."""
    return {item.name: item for item in build_tools(client, user_id)}


async def call(client: MinimallClient, name: str, **kwargs: Any) -> str:
    """调一个工具并返回它回填给模型的文本."""
    return await tools_of(client)[name].fn(**kwargs)


# ---------------------------------------------------------------------------
# 契约: 名字 / 参数 / 是不是异步
# ---------------------------------------------------------------------------


async def test_there_are_exactly_seventeen_tools(client) -> None:
    """正好 17 个, 名字与顺序都钉住 (多一个少一个都是契约变更)."""
    names = tuple(item.name for item in build_tools(client, BUYER_ID))

    assert names == TOOL_NAMES


async def test_every_tool_is_async(client) -> None:
    """17 个工具全是异步函数.

    这不是风格要求: 框架把**同步**工具函数扔进 `asyncio.to_thread` 执行
    (`tool/executor.py` 的 `_invoke`), 同步工具会互相排队占满线程池 ——
    「多个查询同时进行」当场失效 (PRD §4.4). 8 个写工具同样跑在一次问答里,
    它们要是同步的, 一次「加购 + 下单」就能把线程池占住.
    """
    not_async = [
        item.name
        for item in build_tools(client, BUYER_ID)
        if not inspect.iscoroutinefunction(item.fn)
    ]

    assert not_async == [], f"这些工具不是异步函数: {not_async}"


async def test_every_description_says_when_to_use_it(client) -> None:
    """每个工具的说明都写清了「什么时候该用」—— 模型选工具只看这一句.

    判据取「使用」二字: 17 个工具的说明都写成「……时使用」的句式 (见 tools.py).
    这条不检查措辞好不好, 只拦住「忘了写说明」—— 漏写时模型只能靠名字猜.
    写工具格外要紧: `cancel_my_order` 与 `request_refund` 长得像, 模型分不清就
    会拿其中一个去试另一个该做的事.
    """
    without_hint = [
        item.name
        for item in build_tools(client, BUYER_ID)
        if "使用" not in item.description
    ]

    assert without_hint == [], f"这些工具的说明没说什么时候用: {without_hint}"


# ---------------------------------------------------------------------------
# 结果: 商城返回什么, 模型就看到什么
# ---------------------------------------------------------------------------


async def test_search_returns_the_mall_payload_verbatim(client, mall) -> None:
    """搜商品把商城的返回体原样交给模型: 金额是字符串, 库存也在."""
    mall.get(agent_url("products/")).mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "page": 1,
                "page_size": 20,
                "total_pages": 1,
                "results": [
                    {
                        "id": 7,
                        "name": "红米 Note 13",
                        "slug": "redmi-note-13",
                        "price": "1299.00",
                        "stock": 12,
                        "category_name": "手机",
                    }
                ],
            },
        )
    )

    payload = json.loads(await call(client, "search_products", keyword="手机"))

    assert payload["count"] == 1
    # 金额照读: 商城给的是 2 位小数字符串, 不在这里变成 float
    assert payload["results"][0]["price"] == "1299.00"
    assert payload["results"][0]["stock"] == 12


async def test_product_detail_carries_the_current_stock(client, mall) -> None:
    """商品详情带**当前**库存 —— 助手回答「还有货吗」的全部依据."""
    mall.get(agent_url("products/redmi-note-13/")).mock(
        return_value=httpx.Response(
            200, json={"name": "红米 Note 13", "slug": "redmi-note-13", "stock": 3}
        )
    )

    text = await call(client, "get_product_detail", slug="redmi-note-13")

    assert json.loads(text)["stock"] == 3


async def test_an_empty_cart_is_a_normal_answer(client, mall) -> None:
    """从未加购过是空车形态而不是 404 —— 模型该说「购物车是空的」."""
    empty = {"items": [], "total_count": 0, "total_amount": "0.00"}
    mall.get(agent_url("cart/")).mock(return_value=httpx.Response(200, json=empty))

    assert json.loads(await call(client, "get_my_cart")) == empty


async def test_filters_are_forwarded_to_the_mall(client, mall) -> None:
    """模型填的筛选条件原样到达商城 (填了却没用上, 是这类助手最难发现的一类错)."""
    route = mall.get(agent_url("products/")).mock(
        return_value=httpx.Response(200, json={"count": 0, "results": []})
    )

    await call(
        client,
        "search_products",
        keyword="手机",
        min_price=1000,
        max_price=2000,
        ordering="-price",
    )

    # page_size 也在这份参数里: 它是工具的默认值, 每次都会发出去
    # (见 test_the_page_size_defaults_to_a_hundred)
    assert dict(route.calls[0].request.url.params) == {
        "search": "手机",
        "min_price": "1000",
        "max_price": "2000",
        "ordering": "-price",
        "page_size": "100",
    }


@pytest.mark.parametrize(
    ("name", "path"),
    [("search_products", "products/"), ("list_my_orders", "orders/")],
)
async def test_the_page_size_defaults_to_a_hundred(client, mall, name, path) -> None:
    """页大小默认 100 (商城单页上限) 且每次都会发出去 —— 助手读的是整页列表.

    商城侧的默认只有 20 条, 而「我最近买了什么」这类问题一次翻页往往看不全;
    把默认顶到上限, 模型就不必为了看全而自己一页页翻。显式给了页大小则照原样
    发, 不覆盖模型的判断.
    """
    route = mall.get(agent_url(path)).mock(return_value=httpx.Response(200, json={}))

    await tools_of(client)[name].fn()
    assert dict(route.calls[0].request.url.params) == {"page_size": "100"}

    await tools_of(client)[name].fn(page=2, page_size=50)
    assert dict(route.calls[1].request.url.params) == {"page": "2", "page_size": "50"}


@pytest.mark.parametrize("name", ["search_products", "list_my_orders"])
async def test_the_page_size_is_a_closed_set_of_options(client, name: str) -> None:
    """页大小是封闭选项 (5 / 10 / 20 / 50 / 100), 不是任意整数.

    这 5 个值照抄商城买家侧的原接口 (views_buyer.py / views_html.py 的白名单,
    其余值会被回退成默认)。写进 schema 的效果是模型连填错的空间都没有 ——
    填 37 会当场收到校验错误并改用合规值, 而不是发一个会被商城静默改写的结果
    出去 (那种错在答复里看不出来).
    """
    parameters = tools_of(client)[name].parameters

    page_size = parameters["properties"]["page_size"]
    assert page_size["enum"] == [5, 10, 20, 50, 100]
    assert page_size["default"] == 100
    # 有默认值, 所以模型不填也能调 (不算必填)
    assert "page_size" not in parameters.get("required", [])


# ---------------------------------------------------------------------------
# 「没有」与「坏了」: 两种失败走两条路
# ---------------------------------------------------------------------------


async def test_a_missing_product_becomes_a_sentence(client, mall) -> None:
    """查不到商品时返回一句人话 (带上看的是哪个 slug), 而不是抛异常.

    为什么不能抛: 抛出去会变成框架统一的内部错误文案 (「请勿使用相同参数重试」),
    而这里的事实是「你要找的东西不存在」—— 模型本该把它当作答案告诉买家.
    """
    mall.get(agent_url("products/nope/")).mock(
        return_value=httpx.Response(404, json={"detail": "未找到。"})
    )

    text = await call(client, "get_product_detail", slug="nope")

    assert "没有找到" in text
    assert "nope" in text


async def test_a_missing_order_becomes_a_sentence(client, mall) -> None:
    """查不到订单同理 —— 而且这句话要引着买家去核对订单号.

    别人的订单一律查不到 (商城侧按买家过滤), 走的就是这条路径: 模型看到的是
    「没有查到」, 于是它只会说没查到, 不会推测「这单不存在」以外的东西.
    """
    order_no = "202609191230450000039999"
    mall.get(agent_url(f"orders/{order_no}/")).mock(
        return_value=httpx.Response(404, json={"detail": "未找到。"})
    )

    text = await call(client, "get_my_order", order_no=order_no)

    assert "没有查到" in text
    assert order_no in text
    assert "核对" in text


async def test_a_missing_cart_is_never_reported_as_an_empty_cart(client, mall) -> None:
    """购物车端点答 404 时**不能**说「你的购物车是空的」.

    这是一条防编造的用例: 商城用 200 + 空车表示「没加购过」, 所以 `cart/` 的 404
    只可能是「根本没查到这个人」或「打错了地址」。把它说成空车, 助手就会给买家一个
    看起来很正常的假答案 —— 而空车那条路径本该由 `test_an_empty_cart_is_a_normal_answer`
    的 200 响应走.
    """
    mall.get(agent_url("cart/")).mock(
        return_value=httpx.Response(404, json={"detail": "未找到。"})
    )

    with pytest.raises(MinimallError):
        await call(client, "get_my_cart")


async def test_a_mall_failure_is_raised_not_swallowed(client, mall) -> None:
    """商城故障 (非 404) 照旧上抛 —— 交给框架的「内部错误」文案, 不在这里编话.

    这是刻意的分工: 工具只负责把**业务上的「没有」**说清楚, 服务故障说什么话
    由框架统一决定 (那句文案里已经写了「别用相同参数重试」).
    """
    mall.get(agent_url("profile/")).mock(return_value=httpx.Response(500))

    with pytest.raises(MinimallError):
        await call(client, "get_my_profile")


# ---------------------------------------------------------------------------
# 身份: 每个「我的」工具都带着买家 ID (参数表里没有它, 见 test_provider.py)
# ---------------------------------------------------------------------------

# 5 个「我的」工具 → (端点路径, 调用参数)
MY_CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("get_my_cart", "cart/", {}),
    ("list_my_orders", "orders/", {}),
    (
        "get_my_order",
        "orders/202609191230450000031234/",
        {"order_no": "202609191230450000031234"},
    ),
    ("get_my_profile", "profile/", {}),
    ("list_my_addresses", "addresses/", {}),
]


@pytest.mark.parametrize(("name", "path", "kwargs"), MY_CALLS)
async def test_every_my_tool_carries_the_current_buyer(
    client, mall, name: str, path: str, kwargs: dict[str, Any]
) -> None:
    """5 个「我的」工具真的带上了买家身份 —— 「模型看不见」不等于「谁都没看见」."""
    route = mall.get(agent_url(path)).mock(return_value=httpx.Response(200, json={}))

    await tools_of(client)[name].fn(**kwargs)

    assert route.calls[0].request.headers["X-User-Id"] == str(BUYER_ID)


async def test_the_same_client_serves_two_buyers(client, mall) -> None:
    """同一个客户端装两份工具, 只换买家 ID —— 打出去的请求头跟着换.

    身份是**装配时**写进闭包的, 不是请求时从哪儿读的: 换个人装配就是另一个人的
    数据, 而工具本身一行不动.
    """
    route = mall.get(agent_url("cart/")).mock(return_value=httpx.Response(200, json={}))

    await tools_of(client, 9921)["get_my_cart"].fn()

    assert route.calls[0].request.headers["X-User-Id"] == "9921"


# ---------------------------------------------------------------------------
# 8 个写工具: 打对了端点 / 结果照转 / 被拒时说人话
# ---------------------------------------------------------------------------

# 一条订单号 (24 位) 与一个商品 slug: 下面那张表里要重复用好几遍, 提出来免得
# 每行都长到折行 (折行之后"哪个工具打哪条路径"反而看不清)
ORDER_NO = "202609191230450000031234"
SLUG = "redmi-note-13"

# 每个写工具 → (方法, 端点路径, 调用参数, 期望的请求体)
# 请求体那栏是**契约**: 商城侧认的字段名 (issue 11 的清单), 拼错一个就是 400.
WRITE_CALLS: list[tuple[str, str, str, dict[str, Any], dict[str, Any] | None]] = [
    (
        "add_to_cart",
        "POST",
        "cart/items/",
        {"slug": SLUG, "quantity": 2},
        {"slug": SLUG, "quantity": 2},
    ),
    (
        "update_cart_item",
        "PATCH",
        f"cart/items/{SLUG}/",
        {"slug": SLUG, "quantity": 3},
        {"quantity": 3},
    ),
    ("remove_cart_item", "DELETE", f"cart/items/{SLUG}/", {"slug": SLUG}, None),
    ("clear_cart", "DELETE", "cart/clear/", {}, None),
    ("place_order", "POST", "orders/", {}, {}),  # 不带地址 = 用默认地址
    ("place_order", "POST", "orders/", {"address_id": 5}, {"address_id": 5}),
    (
        "cancel_my_order",
        "POST",
        f"orders/{ORDER_NO}/cancel/",
        {"order_no": ORDER_NO},
        None,
    ),
    (
        "request_refund",
        "POST",
        "refunds/",
        {"order_no": ORDER_NO},
        {"order_no": ORDER_NO},
    ),
    ("list_my_refunds", "GET", "refunds/", {}, None),
]


@pytest.mark.parametrize(("name", "method", "path", "kwargs", "body"), WRITE_CALLS)
async def test_every_write_tool_hits_the_right_endpoint(
    client, mall, name: str, method: str, path: str, kwargs: dict, body
) -> None:
    """每个写工具打的是哪个方法 / 哪条路径 / 带什么请求体 —— 逐个钉住.

    写操作拼错字段名不会像只读那样"查不到", 它会**改错东西或者报 400**; 而这两
    种错模型都看不出来 (它只看得到工具回了什么).
    """
    route = mall.request(method, agent_url(path)).mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    text = await call(client, name, **kwargs)

    assert route.called, f"{name} 没有打到 {method} {path}"
    assert json.loads(text) == {"ok": True}
    if body is None:
        assert not route.calls[0].request.content, f"{name} 不该带请求体"
    else:
        assert json.loads(route.calls[0].request.content) == body


@pytest.mark.parametrize(("name", "method", "path", "kwargs", "_body"), WRITE_CALLS)
async def test_every_write_tool_carries_the_current_buyer(
    client, mall, name: str, method: str, path: str, kwargs: dict, _body
) -> None:
    """写工具**也**带着买家身份 —— 身份不在参数表里, 但每次请求都在头上.

    L1a 只断言了 5 个只读工具; 写工具漏带身份就更严重: 商城那边 `X-User-Id` 缺了
    是 400, 而"漏了但被兜默认值"这种更隐蔽的错会去改**别人**的购物车.
    """
    route = mall.request(method, agent_url(path)).mock(
        return_value=httpx.Response(200, json={})
    )

    await call(client, name, **kwargs)

    assert route.calls[0].request.headers["X-User-Id"] == str(BUYER_ID)


async def test_cancelling_reports_what_it_gave_back(client, mall) -> None:
    """取消的回执带着两个副作用数字, 工具原样转达 (模型要念的就是这两句)."""
    mall.request("POST", agent_url("orders/202609191230450000031234/cancel/")).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "cancelled",
                "balance_returned": "1899.00",
                "restocked_count": 1,
            },
        )
    )

    payload = json.loads(
        await call(client, "cancel_my_order", order_no="202609191230450000031234")
    )

    assert payload["balance_returned"] == "1899.00"
    assert payload["restocked_count"] == 1


async def test_a_refused_write_becomes_a_sentence_with_the_next_step(
    client, mall
) -> None:
    """商城按规则拒了 → 工具回一句话 (原话 + 按码补的"下一步"), 而不是抛异常.

    这是写工具与只读工具在失败上的**分界**: 「库存不足」是答案, 模型该把它告诉
    买家; 上抛出去只会变成框架那句「内部错误, 请勿重试」, 而重试恰恰是错的.
    """
    mall.request("POST", agent_url("orders/")).mock(
        return_value=httpx.Response(
            409,
            json={
                "error": {
                    "code": "insufficient_stock",
                    "message": "库存不足, 订单没有下成",
                }
            },
        )
    )

    text = await call(client, "place_order")

    assert "库存不足" in text, "商城的原话要照传 (它比我们更清楚发生了什么)"
    assert "改小数量" in text, "这句话商城没说, 得由按码补的那张表给"
    assert not text.startswith("{"), "不能把错误体当数据回填给模型"


async def test_a_refusal_without_a_known_code_still_says_something(
    client, mall
) -> None:
    """码认不出来时只回商城的原话 —— 少一句提示, 不能说错话 (也不抛)."""
    mall.request("POST", agent_url("refunds/")).mock(
        return_value=httpx.Response(
            409,
            json={"error": {"code": "brand_new_code", "message": "这个操作现在做不了"}},
        )
    )

    text = await call(client, "request_refund", order_no="202609191230450000031234")

    assert text == "操作没有完成: 这个操作现在做不了"


async def test_a_write_fault_is_raised_not_swallowed(client, mall) -> None:
    """不带 `error` 体的 4xx 是**故障** (买家身份无效 / 打错路由), 照旧上抛.

    说成"业务上不行"就是对买家撒谎 —— 而提示词里明写「不编造」. 这条与
    `test_a_missing_cart_is_never_reported_as_an_empty_cart` 是同一条纪律.
    """
    mall.request("POST", agent_url("cart/items/")).mock(
        return_value=httpx.Response(404, json={"detail": "未找到. "})
    )

    with pytest.raises(MinimallError):
        await call(client, "add_to_cart", slug="p")
