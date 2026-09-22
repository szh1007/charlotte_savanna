"""CharApp 测试共享 fixtures: 假商城 (respx) + 现成的客户端与工具集.

两条原则 (与框架的 tests/conftest.py 同一套):

- **默认用例不触网**: 商城走 respx 拦截, 模型走 MockLLM (从 `CharAgent/tests/`
  借, 见 pytest.ini 的 pythonpath) —— 整套测试离线可跑, 不依赖 Django 也不
  依赖真 API.
- **样本按商城真实契约写**: 字段与取值一律照 `app/minimall/serializers_agent.py`
  抄 (金额是 2 位小数字符串, 状态给 code + 中文 label) —— 样本一旦比契约宽松,
  测试就会在真实链路上放行本该红的东西.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx

from CharApp.minimall.client import MinimallClient

# 假的商城地址 (respx 拦截它, 一个包都不出网)
AGENT_BASE_URL = "http://minimall.test/api/minimall/agent/"

# 内部令牌与买家 ID (测试里都是常量, 断言时直接比)
TOKEN = "test-internal-token"
BUYER_ID = 3

# 17 个工具的名字 (顺序即注册顺序: 9 个只读在前, 8 个写接在后面) ——
# 工具与提供者两组用例共用同一份期望
TOOL_NAMES = (
    "search_products",
    "get_product_detail",
    "list_categories",
    "list_featured_products",
    "get_my_cart",
    "list_my_orders",
    "get_my_order",
    "get_my_profile",
    "list_my_addresses",
    "add_to_cart",
    "update_cart_item",
    "remove_cart_item",
    "clear_cart",
    "place_order",
    "cancel_my_order",
    "request_refund",
    "list_my_refunds",
)

# **会改数据**的那 7 个 (护栏的判据是注解, 而注解的含义就是"会改数据").
#
# 为什么不是上面那 8 个: L2 一次加了 8 个工具, 但 `list_my_refunds` 只是**读**
# 退款列表 —— 它不该占买家的写操作预算, 也不该被金额规则管 (见 tools.py 的
# 那一节说明). 「8 个写工具」是这一批的名字, 「7 个会改数据」才是注解的判据.
WRITE_TOOL_NAMES = (
    "add_to_cart",
    "update_cart_item",
    "remove_cart_item",
    "clear_cart",
    "place_order",
    "cancel_my_order",
    "request_refund",
)


def agent_url(path: str) -> str:
    """内部端点的完整地址 (拼法与 `client.py` 的 base_url 一致: 以 `/` 相接)."""
    return f"{AGENT_BASE_URL}{path}"


# ---------------------------------------------------------------------------
# 商城返回的样本 (照 serializers_agent.py 写)
# ---------------------------------------------------------------------------

PRODUCT: dict[str, Any] = {
    "id": 7,
    "name": "红米 Note 13",
    "slug": "redmi-note-13",
    "price": "1299.00",
    "stock": 12,
    "category_name": "手机",
}

ORDER_LIST: dict[str, Any] = {
    "count": 1,
    "page": 1,
    "page_size": 20,
    "total_pages": 1,
    "results": [
        {
            "order_no": "202609191230450000031234",
            "status": "shipped",
            "status_display": "已发货",
            "total_amount": "1899.00",
            "item_count": 1,
            "created_at": "2026-09-19T12:30:45+08:00",
        }
    ],
}

ORDER_DETAIL: dict[str, Any] = {
    "order_no": "202609191230450000031234",
    "status": "shipped",
    "status_display": "已发货",
    "total_amount": "1899.00",
    "shipping_address_snapshot": {"receiver_name": "张三"},
    "items": [
        {
            "product_id": 7,
            "product_name": "红米 Note 13",
            "product_price": "1899.00",
            "quantity": 1,
            "subtotal": "1899.00",
        }
    ],
    "status_timeline": [
        {"status": "pending", "label": "下单", "time": "2026-09-19T12:30:45+08:00"},
        {"status": "paid", "label": "付款", "time": "2026-09-19T12:31:00+08:00"},
    ],
    "created_at": "2026-09-19T12:30:45+08:00",
    "paid_at": "2026-09-19T12:31:00+08:00",
    "shipped_at": "2026-09-19T13:00:00+08:00",
    "received_at": None,
    "cancelled_at": None,
}

PROFILE: dict[str, Any] = {
    "id": BUYER_ID,
    "username": "buyer3",
    "email": "buyer3@example.com",
    "phone": "13800000003",
    "balance": "9500.00",
}

CART: dict[str, Any] = {
    "items": [
        {
            "product_id": 7,
            "product_name": "红米 Note 13",
            "product_slug": "redmi-note-13",
            "product_price": "1299.00",
            "quantity": 2,
            "stock": 12,
            "subtotal": "2598.00",
        }
    ],
    "total_count": 2,
    "total_amount": "2598.00",
}

CATEGORIES: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "数码",
        "slug": "digital",
        "children": [{"id": 2, "name": "手机", "slug": "phone", "children": []}],
    }
]

ADDRESSES: list[dict[str, Any]] = [
    {
        "id": 5,
        "receiver_name": "张三",
        "phone": "13800000003",
        "province": "浙江省",
        "city": "杭州市",
        "district": "西湖区",
        "detail": "文三路 100 号",
        "is_default": True,
    }
]

# 8 个写端点的样本 (方法 + 路径 → 返回体).
#
# 形状与只读那批同源 (写购物车的四个端点回的**也是整车**), 只有两处是写端点独有
# 的: 取消的回执多两个副作用字段 (`balance_returned` / `restocked_count`), 退款
# 那条 `amount` 是 null (还没批准, 金额没协商出来).
ORDER_NO = "202609191230450000031234"

REFUND: dict[str, Any] = {
    "order_no": ORDER_NO,
    "status": "requested",
    "status_display": "待处理",
    "amount": None,
    "admin_note": "",
    "created_at": "2026-09-21T10:00:00+08:00",
    "approved_at": None,
    "refunded_at": None,
    "rejected_at": None,
}

CANCELLED_ORDER: dict[str, Any] = {
    **ORDER_DETAIL,
    "status": "cancelled",
    "status_display": "已取消",
    # 取消只发生在**付款之前** (2026-09-22 改判), 所以这份样本里没有付款与发货的
    # 时间戳, 退给余额的金额恒为 "0.00" —— 待付款的订单从没扣过钱. 样本要是留着
    # 已发货 + 退款 1899.00, 它描述的就是一个不可能存在的状态了.
    "paid_at": None,
    "shipped_at": None,
    "status_timeline": [ORDER_DETAIL["status_timeline"][0]],
    "cancelled_at": "2026-09-21T10:00:00+08:00",
    "balance_returned": "0.00",
    "restocked_count": 1,
}

EMPTY_CART: dict[str, Any] = {"items": [], "total_count": 0, "total_amount": "0.00"}

WRITE_ENDPOINTS: dict[tuple[str, str], Any] = {
    ("POST", "cart/items/"): CART,
    ("PATCH", "cart/items/redmi-note-13/"): CART,
    ("DELETE", "cart/items/redmi-note-13/"): CART,
    ("DELETE", "cart/clear/"): EMPTY_CART,
    ("POST", "orders/"): ORDER_DETAIL,
    ("POST", f"orders/{ORDER_NO}/cancel/"): CANCELLED_ORDER,
    ("POST", "refunds/"): REFUND,
    ("GET", "refunds/"): [REFUND],
}


# 9 个只读端点的完整样本表 (路径 → 返回体); 端到端用例拿它一次性铺满假商城
ENDPOINTS: dict[str, Any] = {
    "products/": {
        "count": 1,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
        "results": [PRODUCT],
    },
    "products/redmi-note-13/": {
        **PRODUCT,
        "description": "性价比之选",
        "is_featured": False,
        "category_tree": [{"id": 2, "name": "手机", "slug": "phone"}],
    },
    "categories/": CATEGORIES,
    "featured-products/": [PRODUCT],
    "cart/": CART,
    "orders/": ORDER_LIST,
    "orders/202609191230450000031234/": ORDER_DETAIL,
    "profile/": PROFILE,
    "addresses/": ADDRESSES,
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mall() -> Iterator[respx.MockRouter]:
    """假商城: 一个不触网的 respx 路由器.

    两条开关的取舍:
    - `assert_all_mocked` 保持默认的 True —— 没注册的请求直接报错, 于是「路径
      拼错了」当场就红, 而不是悄悄连出去.
    - `assert_all_called` 关掉 —— `mock_all` 会把 17 个端点一次铺满 (端到端用例
      需要「模型想调哪个都有得调」), 而每个用例只用到其中一两个.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router


def mock_all(mall: respx.MockRouter) -> dict[str, respx.Route]:
    """把 17 个端点全部挂上 (端到端用例用: 模型想调哪个都有得调).

    返回的字典键**一律是 `"方法 路径"`** (`"GET cart/"` / `"POST cart/items/"`), 用例
    按它去翻哪条被调过. 方法必须进键里: 同一个 `orders/` 上 GET 与 POST 是两条
    路由, 只看路径分不开 —— 与其让 GET 用裸路径、写操作带方法 (两套约定混在一个
    字典里), 不如一律带上.
    """
    routes = {
        f"GET {path}": mall.get(agent_url(path)).mock(
            return_value=httpx.Response(200, json=body)
        )
        for path, body in ENDPOINTS.items()
    }
    routes.update(
        {
            f"{method} {path}": mall.request(method, agent_url(path)).mock(
                return_value=httpx.Response(200, json=body)
            )
            for (method, path), body in WRITE_ENDPOINTS.items()
        }
    )
    return routes


@pytest.fixture
async def client() -> AsyncIterator[MinimallClient]:
    """一个接到假商城上的客户端 (地址与令牌都是测试常量)."""
    instance = MinimallClient(base_url=AGENT_BASE_URL, token=TOKEN)
    yield instance
    await instance.aclose()
