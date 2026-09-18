"""9 个只读工具: 名字 / 参数契约 / 结果与「没有」的翻译.

工具是模型能对这个商城做的**全部**事情, 所以这里测的是它对外的那份契约:

- 名字与参数 (模型看到的就是这些)
- 结果: 商城返回什么, 模型就看到什么 (金额照原样, 不做 float 转换)
- 失败: 404 翻成人话 (「没有查到」), 其余上抛给框架统一的内部错误文案

身份 (user_id) 的守卫不在这里, 在 `test_provider.py` —— 那是提供者契约的一部分.
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


async def test_there_are_exactly_nine_read_only_tools(client) -> None:
    """正好 9 个, 名字与顺序都钉住 (多一个少一个都是契约变更)."""
    names = tuple(item.name for item in build_tools(client, BUYER_ID))

    assert names == TOOL_NAMES


async def test_every_tool_is_async(client) -> None:
    """9 个工具全是异步函数.

    这不是风格要求: 框架把**同步**工具函数扔进 `asyncio.to_thread` 执行
    (`tool/executor.py` 的 `_invoke`), 9 个同步工具会互相排队占满线程池 ——
    「多个查询同时进行」当场失效 (PRD §4.4).
    """
    not_async = [
        item.name
        for item in build_tools(client, BUYER_ID)
        if not inspect.iscoroutinefunction(item.fn)
    ]

    assert not_async == [], f"这些工具不是异步函数: {not_async}"


async def test_every_description_says_when_to_use_it(client) -> None:
    """每个工具的说明都写清了「什么时候该用」—— 模型选工具只看这一句.

    判据取「使用」二字: 9 个工具的说明都写成「……时使用」的句式 (见 tools.py).
    这条不检查措辞好不好, 只拦住「忘了写说明」—— 漏写时模型只能靠名字猜.
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
