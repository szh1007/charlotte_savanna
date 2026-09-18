"""商城客户端 (传输层) 测试: 请求长什么样 / 失败怎么翻.

被测的是「业务侧唯一一处跟商城说话的地方」对外的两个承诺:

1. **每个请求都带上该带的**: 内部令牌 (认证) + 买家身份 (查谁的数据), 可选参数
   没给就不出现在 wire 上.
2. **失败分成两类**: 「按标识符查的东西不存在」是答案 (查无此物), 其余全是故障
   (连不上 / 超时 / 非 2xx / 打到了别的路由 / 响应不是 JSON) —— 前者让工具说人话,
   后者交给框架的内部错误文案. **404 本身不构成「答案」**, 端点与体裁都算数.

商城是假的 (respx), 所以这里既不需要 Django 也不需要网络.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import BUYER_ID, TOKEN, agent_url

from CharApp.minimall.client import MinimallClient, MinimallError, MinimallNotFoundError

# 9 个方法 → (端点路径, 调用参数) —— 遍历用, 加方法时这里也要加一行
CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("search_products", "products/", {"keyword": "手机"}),
    ("get_product_detail", "products/redmi-note-13/", {"slug": "redmi-note-13"}),
    ("list_categories", "categories/", {}),
    ("list_featured_products", "featured-products/", {}),
    ("get_cart", "cart/", {}),
    ("list_orders", "orders/", {}),
    (
        "get_order",
        "orders/202609191230450000031234/",
        {"order_no": "202609191230450000031234"},
    ),
    ("get_profile", "profile/", {}),
    ("list_addresses", "addresses/", {}),
]


def _path_of(method: str) -> str:
    """方法名 → 端点路径 (从上面那张 CALLS 表里查, 免得两处各写一份)."""
    return next(path for name, path, _ in CALLS if name == method)


def ok() -> httpx.Response:
    """一个成功的假响应 (正文用空对象 —— 客户端只解 JSON, 不校验形状)."""
    return httpx.Response(200, json={})


def not_found() -> httpx.Response:
    """商城的 404: DRF 的 JSON 体裁 (`{"detail": ...}`).

    体裁在这里是**判据**而不只是内容 —— 客户端靠它分辨「商城的 404」与「打到
    别的路由拿到的 HTML 404」, 见 `test_a_non_json_404_is_a_failure`.
    """
    return httpx.Response(404, json={"detail": "未找到。"})


# ---------------------------------------------------------------------------
# 请求长什么样
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "kwargs"), CALLS)
async def test_every_call_reaches_its_own_endpoint(
    client: MinimallClient, mall, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    """9 个方法各打各的端点 (路径写错一个就是一个查不到东西的假象)."""
    route = mall.get(agent_url(path)).mock(return_value=ok())

    await getattr(client, method)(user_id=BUYER_ID, **kwargs)

    assert route.called, f"{method} 没打到 {path}"


@pytest.mark.parametrize(("method", "path", "kwargs"), CALLS)
async def test_every_call_carries_the_token_and_the_buyer(
    client: MinimallClient, mall, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    """每个请求都带上内部令牌与买家身份 —— 少一个商城就拒 (那边 fail closed)."""
    route = mall.get(agent_url(path)).mock(return_value=ok())

    await getattr(client, method)(user_id=BUYER_ID, **kwargs)

    headers = route.calls[0].request.headers
    assert headers["X-Internal-Token"] == TOKEN
    assert headers["X-User-Id"] == str(BUYER_ID)


async def test_optional_filters_are_omitted_when_not_given(
    client: MinimallClient, mall
) -> None:
    """没给的可选参数不出现在 wire 上 (发一个空的 category= 等于发了个空筛选)."""
    route = mall.get(agent_url("products/")).mock(return_value=ok())

    await client.search_products(user_id=BUYER_ID, keyword="手机")

    assert dict(route.calls[0].request.url.params) == {"search": "手机"}


async def test_optional_filters_are_sent_when_given(
    client: MinimallClient, mall
) -> None:
    """给了的就照原样发出去 (`keyword` 在 wire 上叫 `search` —— 商城侧的过滤器名)."""
    route = mall.get(agent_url("products/")).mock(return_value=ok())

    await client.search_products(
        user_id=BUYER_ID,
        keyword="手机",
        category="phone",
        min_price=1000,
        max_price=2000,
        ordering="-price",
        page=2,
        page_size=50,
    )

    params = dict(route.calls[0].request.url.params)
    assert params == {
        "search": "手机",
        "category": "phone",
        "min_price": "1000",
        "max_price": "2000",
        "ordering": "-price",
        "page": "2",
        "page_size": "50",
    }


async def test_the_base_url_does_not_swallow_the_last_segment(mall) -> None:
    """base_url 不以 `/` 结尾时也要拼对 —— 否则 httpx 会吃掉最后一段路径.

    这条盯的是一个很容易漏的拼接细节: 使用者把
    CHARAPP_MINIMALL_BASE_URL 写成 `.../agent` (没带尾斜杠) 是极常见的,
    而那时的失败形态是「所有请求都打到 /api/minimall/ 上去」—— 表现为一堆 404,
    排查起来离真正的原因很远.
    """
    route = mall.get(agent_url("profile/")).mock(return_value=ok())
    client = MinimallClient(
        base_url="http://minimall.test/api/minimall/agent", token=TOKEN
    )

    try:
        await client.get_profile(user_id=BUYER_ID)
    finally:
        await client.aclose()

    assert route.called


# ---------------------------------------------------------------------------
# 失败怎么翻
# ---------------------------------------------------------------------------


async def test_404_becomes_not_found(client: MinimallClient, mall) -> None:
    """按标识符查单个资源的 404 单独一个异常 —— 它是答案 (查无此物), 不是故障."""
    mall.get(agent_url("products/nope/")).mock(return_value=not_found())

    with pytest.raises(MinimallNotFoundError) as excinfo:
        await client.get_product_detail(user_id=BUYER_ID, slug="nope")

    assert excinfo.value.status == 404
    # 它同时是 MinimallError 的子类: 只想「失败就报错」的调用方也接得住
    assert isinstance(excinfo.value, MinimallError)


# 集合类端点 → 调用参数 (这些端点的「空」是 200 + 空数组, 不是 404)
COLLECTION_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_products", {"keyword": "手机"}),
    ("list_categories", {}),
    ("list_featured_products", {}),
    ("get_cart", {}),
    ("list_orders", {}),
    ("list_addresses", {}),
    ("get_profile", {}),
]


@pytest.mark.parametrize(("method", "kwargs"), COLLECTION_CALLS)
async def test_a_404_on_a_collection_endpoint_is_a_failure(
    client: MinimallClient, mall, method: str, kwargs: dict[str, Any]
) -> None:
    """集合类端点的 404 **不算答案**, 算故障.

    为什么这是要紧的一条: 商城侧「空」一律是 200 + 空数组, 所以这些端点上的 404
    只可能来自别处 —— 未知买家 (views_agent._resolve_buyer 对不存在的买家返回 404)
    或 `CHARAPP_MINIMALL_BASE_URL` 配错。若把它当「没有」, 助手就会对买家说
    「你的购物车是空的」, 而事实是**压根没查到** —— 正好踩中提示词里「不编造」
    那条禁则.
    """
    mall.get(agent_url(_path_of(method))).mock(return_value=not_found())

    with pytest.raises(MinimallError) as excinfo:
        await getattr(client, method)(user_id=BUYER_ID, **kwargs)

    assert not isinstance(excinfo.value, MinimallNotFoundError)
    assert excinfo.value.status == 404


async def test_a_non_json_404_is_a_failure(client: MinimallClient, mall) -> None:
    """就算按标识符查, 404 的体裁不对也不当答案 —— 多半是基地址配错了.

    打到了别的路由时拿到的是 Django 那张 HTML 404 页; 把它当「商品不存在」说出来,
    买家会以为商城真的没有这件货. 区分方式: DRF 的 404 是 `{"detail": ...}`.
    """
    mall.get(agent_url("products/nope/")).mock(
        return_value=httpx.Response(404, text="<html>Page not found</html>")
    )

    with pytest.raises(MinimallError) as excinfo:
        await client.get_product_detail(user_id=BUYER_ID, slug="nope")

    assert not isinstance(excinfo.value, MinimallNotFoundError)


@pytest.mark.parametrize("status", [400, 403, 500, 502])
async def test_other_error_statuses_become_minimall_error(
    client: MinimallClient, mall, status: int
) -> None:
    """其余非 2xx 一律 MinimallError, 并把状态码与响应摘要带在消息里."""
    mall.get(agent_url("profile/")).mock(
        return_value=httpx.Response(status, text="boom")
    )

    with pytest.raises(MinimallError) as excinfo:
        await client.get_profile(user_id=BUYER_ID)

    assert excinfo.value.status == status
    assert str(status) in str(excinfo.value)
    assert "boom" in str(excinfo.value)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("连接被拒绝"),
        httpx.ReadTimeout("读超时"),
    ],
)
async def test_network_failures_become_minimall_error(
    client: MinimallClient, mall, failure: Exception
) -> None:
    """连不上 / 超时也是 MinimallError (状态码为 None: 请求根本没到商城).

    为什么要在客户端就把 httpx 的异常换掉: 工具层只认本模块这两个异常,
    让 httpx 的原始异常漏出去, 「翻成模型看得懂的话」这件事就漏了一个口子.
    """
    mall.get(agent_url("profile/")).mock(side_effect=failure)

    with pytest.raises(MinimallError) as excinfo:
        await client.get_profile(user_id=BUYER_ID)

    assert excinfo.value.status is None
    assert type(failure).__name__ in str(excinfo.value)


async def test_a_non_json_body_becomes_minimall_error(
    client: MinimallClient, mall
) -> None:
    """200 但正文不是 JSON (打到了别的路由 / 被网关换了页面) 也是故障."""
    mall.get(agent_url("profile/")).mock(
        return_value=httpx.Response(200, text="<html>登录页</html>")
    )

    with pytest.raises(MinimallError) as excinfo:
        await client.get_profile(user_id=BUYER_ID)

    assert "JSON" in str(excinfo.value)
