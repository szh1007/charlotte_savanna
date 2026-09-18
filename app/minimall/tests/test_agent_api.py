"""Agent 内部端点测试 (CharApp issue 02).

接缝: Django 测试客户端直接打 /api/minimall/agent/, 断言三件事 ——
认证 fail closed / 买家数据隔离 / 商品数据不走 Redis 缓存.
"""

import contextlib
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from app.minimall.models import (
    Cart,
    CartItem,
    Category,
    Order,
    OrderItem,
    Product,
    Profile,
    ShippingAddress,
)

User = get_user_model()

TOKEN = "test-internal-token"

# 9 个端点: (url name, 路径参数). 认证测试遍历用, 路径参数取假值 ——
# 权限校验先于数据查询, 因此假 slug / 假订单号也能验证"被拒"这件事.
AGENT_URLS = [
    ("product_list", {}),
    ("product_detail", {"slug": "whatever"}),
    ("category_tree", {}),
    ("featured_products", {}),
    ("cart", {}),
    ("order_list", {}),
    ("order_detail", {"order_no": "whatever"}),
    ("profile", {}),
    ("address_list", {}),
]


def _url(name, kwargs=None):
    return reverse(f"minimall_agent:{name}", kwargs=kwargs or {})


def _clear_minimall_cache():
    """清空 minimall 缓存, 避免跨测试共享真实 Redis 导致数据污染."""
    with contextlib.suppress(Exception):
        cache.delete_pattern("minimall:*")


@override_settings(CHARAPP_INTERNAL_TOKEN=TOKEN)
class AgentAuthTest(TestCase):
    """内部令牌认证 — 全部 9 个端点, 三种失败方式."""

    def setUp(self):
        self.client = APIClient()

    def test_all_endpoints_reject_without_token(self):
        for name, kwargs in AGENT_URLS:
            with self.subTest(endpoint=name):
                r = self.client.get(_url(name, kwargs))
                self.assertEqual(r.status_code, 403)

    def test_all_endpoints_reject_wrong_token(self):
        for name, kwargs in AGENT_URLS:
            with self.subTest(endpoint=name):
                r = self.client.get(_url(name, kwargs), HTTP_X_INTERNAL_TOKEN="wrong")
                self.assertEqual(r.status_code, 403)

    def test_non_ascii_token_rejected(self):
        """非 ASCII 令牌也是"错误令牌", 不能退化成 500 (头值按 latin-1 解码)."""
        r = self.client.get(
            _url("product_list"), HTTP_X_INTERNAL_TOKEN="tok" + chr(233)
        )
        self.assertEqual(r.status_code, 403)

    @override_settings(CHARAPP_INTERNAL_TOKEN="")
    def test_all_endpoints_reject_when_token_unconfigured(self):
        """环境变量没配置 → 拒绝一切请求, 即使请求带了"正确"的令牌."""
        for name, kwargs in AGENT_URLS:
            with self.subTest(endpoint=name):
                r = self.client.get(_url(name, kwargs), HTTP_X_INTERNAL_TOKEN=TOKEN)
                self.assertEqual(r.status_code, 403)


@override_settings(CHARAPP_INTERNAL_TOKEN=TOKEN)
class AgentProductEndpointTest(TestCase):
    """公开商品数据 — 只认令牌, 不要买家身份."""

    def setUp(self):
        _clear_minimall_cache()
        self.client = APIClient()
        self.cat = Category.objects.create(name="Electronics", slug="electronics")
        self.phones = Category.objects.create(
            name="Phones", slug="phones", parent=self.cat
        )
        self.p1 = Product.objects.create(
            name="iPhone", slug="iphone", category=self.phones, price=999, stock=3
        )
        self.p2 = Product.objects.create(
            name="iPad",
            slug="ipad",
            category=self.cat,
            price=599,
            stock=5,
            is_featured=True,
        )
        self.p3 = Product.objects.create(
            name="Hidden",
            slug="hidden",
            category=self.cat,
            price=10,
            stock=1,
            is_active=False,
        )

    def get(self, name, kwargs=None, **params):
        return self.client.get(_url(name, kwargs), params, HTTP_X_INTERNAL_TOKEN=TOKEN)

    def test_all_public_endpoints_work_without_user_header(self):
        """前 4 个是公开数据, 不能强制要求 X-User-Id."""
        public = [
            ("product_list", {}),
            ("product_detail", {"slug": self.p1.slug}),
            ("category_tree", {}),
            ("featured_products", {}),
        ]
        for name, kwargs in public:
            with self.subTest(endpoint=name):
                self.assertEqual(self.get(name, kwargs).status_code, 200)

    def test_product_list_has_stock(self):
        r = self.get("product_list")
        self.assertEqual(r.data["count"], 2)  # p3 下架
        stocks = {p["slug"]: p["stock"] for p in r.data["results"]}
        self.assertEqual(stocks, {"iphone": 3, "ipad": 5})

    def test_search(self):
        r = self.get("product_list", search="iPhone")
        self.assertEqual(r.data["count"], 1)

    def test_category_filter_includes_children(self):
        r = self.get("product_list", category="electronics")
        self.assertEqual(r.data["count"], 2)

    def test_price_filter(self):
        r = self.get("product_list", min_price=600)
        self.assertEqual(r.data["count"], 1)

    def test_ordering(self):
        r = self.get("product_list", ordering="price")
        self.assertEqual(r.data["results"][0]["name"], "iPad")

    def test_pagination(self):
        r = self.get("product_list", page_size=1, page=2)
        self.assertEqual(r.data["count"], 2)
        self.assertEqual(r.data["total_pages"], 2)
        self.assertEqual(len(r.data["results"]), 1)

    def test_product_detail_has_stock(self):
        r = self.get("product_detail", {"slug": "iphone"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["stock"], 3)
        self.assertEqual(r.data["slug"], "iphone")

    def test_product_detail_unknown_slug_404(self):
        r = self.get("product_detail", {"slug": "nope"})
        self.assertEqual(r.status_code, 404)

    def test_product_detail_inactive_404(self):
        r = self.get("product_detail", {"slug": "hidden"})
        self.assertEqual(r.status_code, 404)

    def test_category_tree(self):
        r = self.get("category_tree")
        self.assertEqual(len(r.data), 1)
        self.assertEqual(len(r.data[0]["children"]), 1)
        self.assertEqual(r.data[0]["children"][0]["name"], "Phones")

    def test_featured_products_only_featured(self):
        r = self.get("featured_products")
        self.assertEqual([p["slug"] for p in r.data], ["ipad"])
        self.assertEqual(r.data[0]["stock"], 5)


@override_settings(CHARAPP_INTERNAL_TOKEN=TOKEN)
class AgentCacheBypassTest(TestCase):
    """商品数据直查库 —— 改库存后立刻反映, 且不污染网页接口的缓存."""

    def setUp(self):
        _clear_minimall_cache()
        self.client = APIClient()
        self.cat = Category.objects.create(name="Test", slug="test")
        self.product = Product.objects.create(
            name="P", slug="p", category=self.cat, price=10, stock=3
        )

    def test_stock_update_visible_immediately(self):
        web_url = "/api/minimall/products/p/"
        # 1. 买家网页接口先把旧值写进缓存 (TTL 600s)
        self.assertEqual(self.client.get(web_url).data["stock"], 3)

        # 2. 绕过 signal 直接改库 —— 缓存不会失效
        Product.objects.filter(pk=self.product.pk).update(stock=99)

        # 3. agent 端点必须读到新值 (走了缓存就会是 3)
        r = self.client.get(
            _url("product_detail", {"slug": "p"}), HTTP_X_INTERNAL_TOKEN=TOKEN
        )
        self.assertEqual(r.data["stock"], 99)

        # 4. 对照: 网页接口仍返回缓存里的旧值 (证明 agent 没写缓存)
        self.assertEqual(self.client.get(web_url).data["stock"], 3)


@override_settings(CHARAPP_INTERNAL_TOKEN=TOKEN)
class AgentUserEndpointTest(TestCase):
    """买家私有数据 — 令牌 + X-User-Id, 且 A 拿不到 B 的任何数据."""

    def setUp(self):
        _clear_minimall_cache()
        self.client = APIClient()
        self.buyer = User.objects.create_user(
            username="buyer", email="buyer@t.com", password="pass"
        )
        Profile.objects.create(
            user=self.buyer, balance=Decimal("9500.00"), phone="13800000000"
        )
        self.other = User.objects.create_user(
            username="other", email="other@t.com", password="pass"
        )
        Profile.objects.create(user=self.other, balance=Decimal("1.00"))

        self.cat = Category.objects.create(name="Test", slug="test")
        self.product = Product.objects.create(
            name="P", slug="p", category=self.cat, price=10, stock=5
        )

        self.my_addr = ShippingAddress.objects.create(
            user=self.buyer,
            receiver_name="买家",
            phone="138",
            province="A",
            city="B",
            district="C",
            detail="D",
            is_default=True,
        )
        ShippingAddress.objects.create(
            user=self.other,
            receiver_name="别人",
            phone="139",
            province="A",
            city="B",
            district="C",
            detail="E",
        )

        CartItem.objects.create(
            cart=Cart.objects.create(user=self.buyer),
            product=self.product,
            quantity=3,
        )
        CartItem.objects.create(
            cart=Cart.objects.create(user=self.other),
            product=self.product,
            quantity=1,
        )

        self.other_order = Order.objects.create(
            order_no="202609180000000000000001",
            user=self.other,
            status=Order.Status.PAID,
            total_amount=Decimal("20.00"),
            shipping_address_snapshot={"receiver_name": "别人", "detail": "E"},
            paid_at=timezone.now(),
        )
        OrderItem.objects.create(
            order=self.other_order,
            product=self.product,
            product_name="P",
            product_price=Decimal("10.00"),
            quantity=2,
            subtotal=Decimal("20.00"),
        )

    def get(self, name, kwargs=None, user=None, **params):
        headers = {"HTTP_X_INTERNAL_TOKEN": TOKEN}
        if user is not False:
            headers["HTTP_X_USER_ID"] = str((user or self.buyer).id)
        return self.client.get(_url(name, kwargs), params, **headers)

    # --- 购物车 -----------------------------------------------------------

    def test_cart_returns_own_items(self):
        r = self.get("cart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["total_count"], 3)
        self.assertEqual(r.data["total_amount"], "30.00")
        self.assertEqual(r.data["items"][0]["product_slug"], "p")
        self.assertEqual(r.data["items"][0]["stock"], 5)

    def test_cart_isolation(self):
        r = self.get("cart", user=self.other)
        self.assertEqual(r.data["total_count"], 1)

    def test_cart_does_not_create_cart_row(self):
        """只读接口: 没有购物车行时返回空购物车, 不产生写副作用."""
        user = User.objects.create_user(
            username="nocart", email="nc@t.com", password="pass"
        )
        Profile.objects.create(user=user, balance=Decimal("0.00"))
        r = self.get("cart", user=user)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.data, {"items": [], "total_count": 0, "total_amount": "0.00"}
        )
        self.assertFalse(Cart.objects.filter(user=user).exists())

    # --- 订单 -------------------------------------------------------------

    def test_order_list_returns_own_orders(self):
        r = self.get("order_list")
        self.assertEqual(r.data["count"], 0)

    def test_order_detail_of_other_user_404(self):
        r = self.get("order_detail", {"order_no": self.other_order.order_no})
        self.assertEqual(r.status_code, 404)

    def test_order_detail_returns_own_order(self):
        order = Order.objects.create(
            order_no="202609180000000000000002",
            user=self.buyer,
            status=Order.Status.PAID,
            total_amount=Decimal("20.00"),
            shipping_address_snapshot={"receiver_name": "买家", "detail": "D"},
            paid_at=timezone.now(),
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name="P",
            product_price=Decimal("10.00"),
            quantity=2,
            subtotal=Decimal("20.00"),
        )
        r = self.get("order_detail", {"order_no": order.order_no})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status_display"], "已付款")
        self.assertEqual(r.data["items"][0]["quantity"], 2)
        self.assertEqual(
            [e["status"] for e in r.data["status_timeline"]], ["pending", "paid"]
        )

    # --- 账户 -------------------------------------------------------------

    def test_profile_returns_balance(self):
        r = self.get("profile")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["balance"], "9500.00")
        self.assertEqual(r.data["username"], "buyer")

    def test_profile_without_profile_row(self):
        """管理员账号 (无 minimall_profile) 不能把接口打崩."""
        staff = User.objects.create_user(
            username="staff", email="s@t.com", password="pass", is_staff=True
        )
        r = self.get("profile", user=staff)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["phone"])
        self.assertEqual(r.data["balance"], "0.00")

    def test_addresses_isolation(self):
        r = self.get("address_list")
        self.assertEqual(len(r.data), 1)
        self.assertEqual(r.data[0]["receiver_name"], "买家")

    # --- 身份头 -----------------------------------------------------------

    def test_missing_user_header_400(self):
        r = self.get("cart", user=False)
        self.assertEqual(r.status_code, 400)

    def test_nonnumeric_user_header_400(self):
        r = self.client.get(
            _url("cart"), HTTP_X_INTERNAL_TOKEN=TOKEN, HTTP_X_USER_ID="abc"
        )
        self.assertEqual(r.status_code, 400)

    def test_unknown_user_id_404(self):
        r = self.client.get(
            _url("cart"), HTTP_X_INTERNAL_TOKEN=TOKEN, HTTP_X_USER_ID="99999999"
        )
        self.assertEqual(r.status_code, 404)

    def test_out_of_range_user_id_404(self):
        """超出主键范围的数字不该变成 500."""
        r = self.client.get(
            _url("cart"), HTTP_X_INTERNAL_TOKEN=TOKEN, HTTP_X_USER_ID="9" * 30
        )
        self.assertEqual(r.status_code, 404)
