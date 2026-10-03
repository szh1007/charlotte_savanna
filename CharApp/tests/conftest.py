"""CharApp 测试共享 fixtures: 假商城 (respx) + 现成的客户端与工具集.

两条原则 (与框架的 tests/conftest.py 同一套):

- **默认用例不触网**: 商城走 respx 拦截, 模型走 MockLLM (从 `CharAgent/tests/`
  借, 见 pytest.ini 的 pythonpath) —— 整套测试离线可跑, 不依赖 Django 也不
  依赖真 API.
- **样本按商城真实契约写**: 字段与取值一律照 `app/minimall/serializers_agent.py`
  抄 (金额是 2 位小数字符串, 状态给 code + 中文 label) —— 样本一旦比契约宽松,
  测试就会在真实链路上放行本该红的东西.

**样本与假商城的本体自 issue 41 起住在 `CharApp/eval/fixtures.py`**: 离线跑分器
不是 pytest 用例 (它要落报告 / 要比两份文件 / 要传参数), 于是吃不到 fixture ——
把样本搬进 eval 模块之后, 跑分器与测试用的是**同一份** (方向是测试 → eval, 生产
不依赖测试). 本文件从那里 import 并转发, 既有用例的 `from conftest import ...`
一行都不用改.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import respx

from CharAgent.tests.doubles import no_backup_endpoint as no_backup_endpoint
from CharApp.eval.fixtures import (
    ADDRESSES,
    AGENT_BASE_URL,
    BUYER_ID,
    CANCELLED_ORDER,
    CART,
    CATEGORIES,
    EMPTY_CART,
    ENDPOINTS,
    ORDER_DETAIL,
    ORDER_LIST,
    ORDER_NO,
    PAID_ORDER,
    PAYMENT_PASSWORD,
    PRODUCT,
    PROFILE,
    REFUND,
    SENSITIVE_VALUES,
    TOKEN,
    WRITE_ENDPOINTS,
    agent_url,
    build_mall,
    mock_all,
)
from CharApp.minimall.client import MinimallClient

# 全部工具的名字 (顺序即注册顺序: 只读的在前, 会改数据的与那个读退款的接在后面,
# 代付收尾) —— **这是这份名单的唯一权威**: 工具的契约用例与提供者用例都比对它,
# 数量变了改这里.
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
    "pay_my_order",
)

# **会改数据**的那 8 个 (护栏的判据是注解, 而注解的含义就是"会改数据").
#
# 为什么不是上面那 9 个: L2 一次加了 8 个工具, 但 `list_my_refunds` 只是**读**
# 退款列表 —— 它不该占买家的写操作预算, 也不该被金额规则管 (见 tools.py 的
# 那一节说明). 「9 个写工具」是这两批的名字, 「8 个会改数据」才是注解的判据.
#
# `pay_my_order` (issue 35) 排在最后: 它同样是**写**, 只是它的"改"要买家本人点
# 一次头 (护栏让它挂起, 见 guardrail.py) —— 注解与预算照旧适用.
WRITE_TOOL_NAMES = (
    "add_to_cart",
    "update_cart_item",
    "remove_cart_item",
    "clear_cart",
    "place_order",
    "cancel_my_order",
    "request_refund",
    "pay_my_order",
)

# 从 `CharApp/eval/fixtures.py` 转发出来的那一批 (见模块 docstring): 列在这里是
# 为了说明「它们是**有意**再导出给用例的」—— 少了这份清单, ruff 的 F401 会把它们
# 当成没用的 import 删掉 (本文件确实一个都不直接用).
__all__ = [
    "ADDRESSES",
    "AGENT_BASE_URL",
    "BUYER_ID",
    "CANCELLED_ORDER",
    "CART",
    "CATEGORIES",
    "EMPTY_CART",
    "ENDPOINTS",
    "ORDER_DETAIL",
    "ORDER_LIST",
    "ORDER_NO",
    "PAID_ORDER",
    "PAYMENT_PASSWORD",
    "PRODUCT",
    "PROFILE",
    "REFUND",
    "SENSITIVE_VALUES",
    "TOKEN",
    "TOOL_NAMES",
    "WRITE_ENDPOINTS",
    "WRITE_TOOL_NAMES",
    "agent_url",
    "build_mall",
    "client",
    "mall",
    "mock_all",
]


@pytest.fixture
def mall() -> Iterator[respx.MockRouter]:
    """假商城: 一个不触网的 respx 路由器 (本体在 `eval/fixtures.build_mall`)."""
    with build_mall() as router:
        yield router


@pytest.fixture
async def client() -> AsyncIterator[MinimallClient]:
    """一个接到假商城上的客户端 (地址与令牌都是测试常量)."""
    instance = MinimallClient(base_url=AGENT_BASE_URL, token=TOKEN)
    yield instance
    await instance.aclose()
