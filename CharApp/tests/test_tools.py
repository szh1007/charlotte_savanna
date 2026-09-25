"""18 个工具: 名字 / 参数契约 / 结果与两种失败的翻译.

工具是模型能对这个商城做的**全部**事情, 所以这里测的是它对外的那份契约:

- 名字与参数 (模型看到的就是这些)
- 结果: 商城返回什么, 模型就看到什么 (金额照原样, 不做 float 转换)
- 失败: 只读的 404 翻成人话 (「没有查到」), 写操作被商城按规则拒了抛
  `RefusedActionError` (码 + 中文原话 + 一句下一步 —— 框架把它记成 `status=error`),
  其余上抛给框架统一的内部错误文案

身份 (user_id) 的守卫不在这里, 在 `test_provider.py` —— 那是提供者契约的一部分;
护栏 (写预算 / 金额上限) 在 `test_guardrail.py`, 那是插件的事, 不是工具的.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from conftest import BUYER_ID, ORDER_NO, PAYMENT_PASSWORD, TOOL_NAMES, agent_url

from CharAgent.agent.utils.events import tool_result_data
from CharAgent.tests.mock_llm import make_tool_call
from CharAgent.tool import Tool, ToolActionableError, execute_tool
from CharApp.minimall.client import MinimallClient, MinimallError
from CharApp.minimall.tools import (
    PAYMENT_PASSWORD_FIELD,
    RefusedActionError,
    build_tools,
)


def tools_of(
    client: MinimallClient,
    user_id: int = BUYER_ID,
    *,
    one_shot: Mapping[str, str] | None = None,
) -> dict[str, Tool]:
    """工具名 → 工具 (装配是纯函数, 每个用例现装一份, 互不干扰)."""
    return {item.name: item for item in build_tools(client, user_id, one_shot=one_shot)}


async def call(
    client: MinimallClient,
    name: str,
    *,
    one_shot: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> str:
    """调一个工具并返回它回填给模型的文本 (代付那一条要带一次性载荷)."""
    return await tools_of(client, one_shot=one_shot)[name].fn(**kwargs)


# ---------------------------------------------------------------------------
# 契约: 名字 / 参数 / 是不是异步
# ---------------------------------------------------------------------------


async def test_there_are_exactly_eighteen_tools(client) -> None:
    """正好 18 个, 名字与顺序都钉住 (多一个少一个都是契约变更)."""
    names = tuple(item.name for item in build_tools(client, BUYER_ID))

    assert names == TOOL_NAMES


async def test_every_tool_is_async(client) -> None:
    """18 个工具全是异步函数.

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

    判据取「使用」二字: 18 个工具的说明都写成「……时使用」的句式 (见 tools.py).
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
# 每行都长到折行 (折行之后"哪个工具打哪条路径"反而看不清). 订单号取自 conftest
# —— 假商城的 mock 路由按那个值挂, 两处各写一份就会有一处打不中.
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
    """取消的回执带着回滚件数, 工具原样转达 (模型要念的就是这个数).

    余额恒为 "0.00" (只有待付款的订单能取消, 那种单从没扣过钱) —— 一并断言是为了
    钉住「工具不改商城给的数字」这件事: 原样转达, 不做 float 转换也不做四舍五入.
    """
    mall.request("POST", agent_url("orders/202609191230450000031234/cancel/")).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "cancelled",
                "balance_returned": "0.00",
                "restocked_count": 1,
            },
        )
    )

    payload = json.loads(
        await call(client, "cancel_my_order", order_no="202609191230450000031234")
    )

    assert payload["balance_returned"] == "0.00"
    assert payload["restocked_count"] == 1


async def test_a_refused_write_carries_the_sentence_and_the_next_step(
    client, mall
) -> None:
    """商城按规则拒了 → 抛 `RefusedActionError`, 消息是「原话 + 按码补的下一步」.

    文案与从前**一字不差** (模型收到的文本没变), 变的是它抛出去而不是当返回值:
    返回的话框架把这次执行记成成功 (`status=ok`), 页面按完成态话术渲染 —— 买家看到
    「订单已提交」而订单根本没动 (缺陷 C, 2026-09-22 真机验收点出).

    「库存不足」是商城给**买家**的答案, 不等于这次调用成功 —— 两者是两件事.
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

    with pytest.raises(RefusedActionError) as excinfo:
        await call(client, "place_order")

    text = str(excinfo.value)
    assert "库存不足" in text, "商城的原话要照传 (它比我们更清楚发生了什么)"
    assert "改小数量" in text, "这句话商城没说, 得由按码补的那张表给"
    assert not text.startswith("{"), "不能把错误体当数据回填给模型"
    assert excinfo.value.code == "insufficient_stock", "错误码留给将来的插件看"


async def test_a_refusal_without_a_known_code_still_says_something(
    client, mall
) -> None:
    """码认不出来时只带商城的原话 —— 少一句提示, 不能说错话."""
    mall.request("POST", agent_url("refunds/")).mock(
        return_value=httpx.Response(
            409,
            json={"error": {"code": "brand_new_code", "message": "这个操作现在做不了"}},
        )
    )

    with pytest.raises(RefusedActionError) as excinfo:
        await call(client, "request_refund", order_no="202609191230450000031234")

    assert str(excinfo.value) == "操作没有完成: 这个操作现在做不了"
    assert excinfo.value.code == "brand_new_code"


async def test_a_refused_write_is_recorded_as_an_error(client, mall) -> None:
    """被拒的写操作在框架那边必须是 `status=error` —— 缺陷 C 的守卫.

    这条走框架的**执行入口** (`execute_tool`) 而不是直接调工具函数: 页面上出现
    failed 话术、模型收到哪个文案, 都取决于这里怎么判定. 少了它, 哪天把「抛」改回
    「返回」也不会有人发现 —— 那次真机验收就是这么漂过去的.
    """
    mall.request("POST", agent_url("orders/")).mock(
        return_value=httpx.Response(
            409, json={"error": {"code": "insufficient_stock", "message": "库存不足"}}
        )
    )
    tool_call = make_tool_call("place_order", "{}")

    execution = await execute_tool(tools_of(client)["place_order"], arguments="{}")
    payload = tool_result_data(tool_call, execution, turn=1)

    assert execution.ok is False, "被拒的调用不能记成成功"
    assert "库存不足" in execution.error
    assert payload["status"] == "error"
    # 页面上出现的是按 `status` 选的那句中文 (脱敏层换掉 `error`, 见 ADR-0003), 而
    # 模型读到的就是这句 error —— 两者同源, 所以这里断言的是"文案没在事件里丢".
    assert payload["error"] == execution.error


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


# ---------------------------------------------------------------------------
# 代付 (issue 35): 密码从闭包来, 从不进参数表也不进商城之外的地方
# ---------------------------------------------------------------------------
# 这一节测的是 ADR-0015 那条通路在工具这一层的样子. 三件事分开测:
#
#   - **到得了**: 闭包里的密码进了请求体 (没有它, 密码就没送到商城);
#   - **不该去的时候不去**: 闭包里没有密码时一次端点都不打 (空密码撞一次只会
#     白烧一条失败路径);
#   - **失败说人话**: 密码错那一条要明说"不要重试"(密码是一次性的).
#
# 「密码不在 schema 里」那条守卫在 `test_provider.py` (与身份那条同一个判据).

PASSWORD = PAYMENT_PASSWORD

# 一次性载荷的两种写法: 装配时从运行上下文挑出来的, 或空 (普通提问那一路)
PAYLOAD: dict[str, str] = {PAYMENT_PASSWORD_FIELD: PASSWORD}


def _pay_url() -> str:
    return agent_url(f"orders/{ORDER_NO}/pay/")


def _paid_receipt() -> httpx.Response:
    """商城付款成功时的回执 (字段照 `AgentOrderPaidSerializer` 抄)."""
    return httpx.Response(
        200,
        json={
            "order_no": ORDER_NO,
            "status": "paid",
            "status_display": "已付款",
            "total_amount": "2598.00",
            "balance_remaining": "8101.00",
        },
    )


async def test_pay_sends_the_closure_password_in_the_body(client, mall) -> None:
    """密码进了请求体 —— 而它是从**闭包**来的, 不是从参数表来的.

    请求体那一栏是契约: 商城侧认的字段名, 拼错一个就是 400 (密码错还是 400,
    工具层分不出「字段名错了」与「密码错了」—— 因为两边都是 400).
    """
    route = mall.request("POST", _pay_url()).mock(return_value=_paid_receipt())

    text = await call(client, "pay_my_order", order_no=ORDER_NO, one_shot=PAYLOAD)

    assert route.called, "代付没有打到付款端点"
    assert json.loads(route.calls[0].request.content) == {
        PAYMENT_PASSWORD_FIELD: PASSWORD
    }
    assert json.loads(text)["balance_remaining"] == "8101.00", "回执要照转给模型"
    assert PASSWORD not in text, "回填给模型的那段文本里不该有密码原文"


@pytest.mark.parametrize(
    "one_shot",
    [
        None,  # 这次运行压根没有载荷 (普通提问)
        {},  # 恢复了, 但用户什么都没输
        {"something_else": PASSWORD},  # 键名不对 —— 与没给等价
        {PAYMENT_PASSWORD_FIELD: ""},  # 给了个空的
    ],
    ids=["no-payload", "empty-payload", "wrong-key", "empty-password"],
)
async def test_pay_without_a_password_never_calls_the_mall(
    client, mall, one_shot: dict[str, str] | None
) -> None:
    """闭包里没有密码 → **一次端点都不打**, 回一句「没有拿到授权」.

    这是本片最容易做错的一处: 顺手用空密码去撞一下, 看起来"至少试过了", 实际是
    拿买家的失败次数换我们自己的一次偷懒 —— 还可能把账号锁进某种风控.

    断言落在两处: 端点**零调用**(用打桩的调用记录) 与 消息说清了"没有执行".
    抛而不返回的理由与 `RefusedActionError` 同一条 (见那张用例的 docstring).
    """
    route = mall.request("POST", _pay_url()).mock(return_value=_paid_receipt())

    with pytest.raises(ToolActionableError) as excinfo:
        await call(client, "pay_my_order", order_no=ORDER_NO, one_shot=one_shot)

    assert not route.called, "没有密码就不该打商城"
    text = str(excinfo.value)
    assert "没有执行" in text, "别让模型以为付了"
    assert "不要重试" in text
    assert "密码" in text, "下一步是让买家重新输一次密码, 这句话得说出来"


async def test_a_wrong_password_says_dont_retry(client, mall) -> None:
    """密码错 → 商城的原话 + 「不要重试」(ADR-0015 点名要写死的那一句).

    为什么这句非写不可: 密码是一次性的 (它随这次恢复一起消失), 模型重试拿的是
    同一个已经不在的载荷 —— 撞的还是同一个结果, 而每一次撞都是一次真实失败
    (还可能撞进风控).

    顺带钉住**这条错误消息里没有密码原文**: 它会回填给模型、也会被日志与轨迹记下来
    (它是 `RefusedActionError` 的消息), 而"密码不进任何一句会留痕的文本"正是 issue
    29 那条线的业务侧半边 —— 商城给的那句「支付密码错误」本来就不含它, 这条守着
    将来别有人顺手把请求体拼进错误里.
    """
    mall.request("POST", _pay_url()).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": "payment_failed", "message": "支付密码错误"}},
        )
    )

    with pytest.raises(RefusedActionError) as excinfo:
        await call(client, "pay_my_order", order_no=ORDER_NO, one_shot=PAYLOAD)

    text = str(excinfo.value)
    assert "支付密码错误" in text, "商城的原话照传"
    assert "不要" in text and "重试" in text, "重试拿的是同一个已经不存在的载荷"
    assert "重新说一次" in text, "要给出下一步 —— 否则模型只会再调一次"
    assert excinfo.value.code == "payment_failed"
    assert PASSWORD not in text, "错误消息会进日志与轨迹, 里面不该有密码原文"


async def test_a_paid_order_is_reported_with_its_next_step(client, mall) -> None:
    """已付款的单再付 → 商城说「状态不允许」, 按码补上「去看订单状态」.

    与密码错分开: 这一条**不该重试是同一句话的不同理由** —— 一个是密码用掉了,
    一个是这单本来就付过了. 两个码两条文案, 模型才不会把前者说成后者.
    """
    mall.request("POST", _pay_url()).mock(
        return_value=httpx.Response(
            409,
            json={
                "error": {
                    "code": "invalid_order_status",
                    "message": "订单当前的状态不允许这个操作",
                }
            },
        )
    )

    with pytest.raises(RefusedActionError) as excinfo:
        await call(client, "pay_my_order", order_no=ORDER_NO, one_shot=PAYLOAD)

    assert "订单详情" in str(excinfo.value)
    assert excinfo.value.code == "invalid_order_status"


async def test_insufficient_balance_points_at_recharging(client, mall) -> None:
    """余额不够 → 「先去充值」—— 这一条是唯一"还能救"的付款失败."""
    mall.request("POST", _pay_url()).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": "insufficient_balance", "message": "余额不足"}},
        )
    )

    with pytest.raises(RefusedActionError) as excinfo:
        await call(client, "pay_my_order", order_no=ORDER_NO, one_shot=PAYLOAD)

    assert "充值" in str(excinfo.value)


async def test_the_pay_tool_asks_for_no_password_at_all(client) -> None:
    """工具签名里只有 `order_no` —— 这一条是"密码只能在闭包里"的机械证据.

    schema 那条断言 (test_provider) 看的是生成出来的参数表; 这一条看的是**函数
    本体的签名**: 连一个给密码留的位置都没有. 两者是同一件事的两面 —— 前者能被
    一次 schema 后处理绕过去, 后者不能.
    """
    pay = tools_of(client)["pay_my_order"]

    assert set(inspect.signature(pay.fn).parameters) == {"order_no"}


# 端到端那一层 (装配 → 工具 → 商城) 里, 载荷到不了工具时的行为由
# `test_pay_without_a_password_never_calls_the_mall` 钉住; `one_shot` 这条通道
# 在**提供者**那一侧的样子见 `test_provider.test_the_one_shot_payload_reaches_...`.
