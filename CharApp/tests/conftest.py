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

# 9 个只读工具的名字 (顺序即注册顺序) —— 工具与提供者两组用例共用同一份期望
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
    - `assert_all_called` 关掉 —— `mock_all` 会把 9 个端点一次铺满 (端到端用例
      需要「模型想调哪个都有得调」), 而每个用例只用到其中一两个.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router


def mock_all(mall: respx.MockRouter) -> dict[str, respx.Route]:
    """把 9 个端点全部挂上 (端到端用例用: 模型想调哪个都有得调)."""
    return {
        path: mall.get(agent_url(path)).mock(
            return_value=httpx.Response(200, json=body)
        )
        for path, body in ENDPOINTS.items()
    }


@pytest.fixture
async def client() -> AsyncIterator[MinimallClient]:
    """一个接到假商城上的客户端 (地址与令牌都是测试常量)."""
    instance = MinimallClient(base_url=AGENT_BASE_URL, token=TOKEN)
    yield instance
    await instance.aclose()
