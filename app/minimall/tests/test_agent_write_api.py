"""Agent 写端点测试 (CharApp issue 11).

接缝: Django 测试客户端直接打 /api/minimall/agent/ 的 9 个写端点 (issue 11 的
8 个 + issue 35 的代付).
业务规则本身归 `test_services.py`, 这里管的是「助手那条路走不走得通」——
动作真改了库 / 买家之间互相够不着 / 每种业务拒绝都带自己的错误码.

写端点一律直查库走 service, 不碰 Redis: 有一条用例专门证明这件事 (绕过缓存改库存,
下单必须按新库存判) —— 缓存里的库存可能已经过期十分钟, 拿它判「还能不能下单」
是会出真错的.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from app.minimall.models import (
    Cart,
    CartItem,
    Category,
    Order,
    Product,
    Profile,
    RefundRequest,
    ShippingAddress,
)
from app.minimall.services import (
    OrderServiceError,
    approve_refund,
    create_order,
    pay_order,
)
from app.minimall.views_agent import ERROR_CODES, EXCEPTION_CODES

User = get_user_model()

TOKEN = "test-internal-token"

# 代付用的两个密码 (issue 35): 一个是这个买家设的那个 (setUp 里设的), 一个是
# 故意错的. 提出来是为了让「哪条用例在验密码本身」一眼看得出来.
GOOD_PASSWORD = "123456"
WRONG_PASSWORD = "000000"


def _url(name, kwargs=None):
    return reverse(f"minimall_agent:{name}", kwargs=kwargs or {})


@override_settings(CHARAPP_INTERNAL_TOKEN=TOKEN)
class AgentWriteTestBase(TestCase):
    """一个买家 (buyer) 与一个别人 (other); 商品 10.00 元, 库存 10, 默认地址一条."""

    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(
            username="buyer", email="buyer@t.com", password="pass"
        )
        profile = Profile.objects.create(user=self.buyer, balance=Decimal("10000.00"))
        profile.set_payment_password("123456")
        profile.save()

        self.other = User.objects.create_user(
            username="other", email="other@t.com", password="pass"
        )
        Profile.objects.create(user=self.other, balance=Decimal("10000.00"))

        self.cat = Category.objects.create(name="Test", slug="test")
        self.product = Product.objects.create(
            name="P", slug="p", category=self.cat, price=Decimal("10.00"), stock=10
        )
        self.address = ShippingAddress.objects.create(
            user=self.buyer,
            receiver_name="买家",
            phone="138",
            province="A",
            city="B",
            district="C",
            detail="D",
            is_default=True,
        )
        self.other_address = ShippingAddress.objects.create(
            user=self.other,
            receiver_name="别人",
            phone="139",
            province="A",
            city="B",
            district="C",
            detail="E",
            is_default=True,
        )

    # ------------------------------------------------------------------
    # 搭台用的助手 (被测的是端点, 这些一律走 service / ORM)
    # ------------------------------------------------------------------

    def call(self, method, name, kwargs=None, *, user=None, body=None):
        """打一个写端点; user=False 表示**不带** X-User-Id 头."""
        headers = {"HTTP_X_INTERNAL_TOKEN": TOKEN}
        if user is not False:
            headers["HTTP_X_USER_ID"] = str((user or self.buyer).pk)
        handler = getattr(self.client, method)
        path = _url(name, kwargs)
        if body is None:
            return handler(path, **headers)
        return handler(path, body, format="json", **headers)

    def get(self, name, kwargs=None, *, user=None):
        return self.call("get", name, kwargs, user=user)

    def _add_item(self, quantity=2) -> CartItem:
        cart = Cart.objects.get_or_create(user=self.buyer)[0]
        return CartItem.objects.create(
            cart=cart, product=self.product, quantity=quantity
        )

    def _paid_order(self) -> Order:
        """一张已付款订单 (加购 → 下单 → 付款都走 service, 不经过被测端点)."""
        item = self._add_item(2)
        order = create_order(self.buyer, [item.id], self.address.id)
        pay_order(order, "123456")
        return Order.objects.get(pk=order.pk)

    def _balance(self, user=None) -> Decimal:
        return Profile.objects.get(user=user or self.buyer).balance

    def code(self, response) -> str:
        return response.data["error"]["code"]


class AgentCartWriteTest(AgentWriteTestBase):
    """购物车四个写操作 —— 全部用 slug 定位, 回的都是**动作之后的整车**."""

    def test_add_returns_cart_with_new_item(self):
        response = self.call("post", "cart_item_add", body={"slug": "p", "quantity": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 2)
        self.assertEqual(response.data["total_amount"], "20.00")
        self.assertEqual(response.data["items"][0]["product_slug"], "p")
        self.assertEqual(CartItem.objects.get(cart__user=self.buyer).quantity, 2)

    def test_add_accumulates_on_second_call(self):
        """同一商品再加一次是累加, 不是新增一行 (unique_together 保证只有一行)."""
        self.call("post", "cart_item_add", body={"slug": "p", "quantity": 2})
        response = self.call("post", "cart_item_add", body={"slug": "p", "quantity": 3})

        self.assertEqual(response.data["total_count"], 5)
        self.assertEqual(CartItem.objects.filter(cart__user=self.buyer).count(), 1)

    def test_add_caps_quantity_at_stock(self):
        """要 99 件而库存 10 —— 给 10 件 (与页面同一条截断规则, 助手念的就是这个数)."""
        response = self.call(
            "post", "cart_item_add", body={"slug": "p", "quantity": 99}
        )

        self.assertEqual(response.data["total_count"], 10)

    def test_add_unknown_slug_404(self):
        response = self.call(
            "post", "cart_item_add", body={"slug": "nope", "quantity": 1}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "product_unavailable")

    def test_add_inactive_product_404(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)

        response = self.call("post", "cart_item_add", body={"slug": "p", "quantity": 1})

        self.assertEqual(self.code(response), "product_unavailable")

    def test_add_out_of_stock_409(self):
        Product.objects.filter(pk=self.product.pk).update(stock=0)

        response = self.call("post", "cart_item_add", body={"slug": "p", "quantity": 1})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.code(response), "out_of_stock")

    def test_add_does_not_touch_other_cart(self):
        """A 加购只进 A 的车 —— 身份来自 X-User-Id, 请求体里没有能指名别人车的字段."""
        self.call("post", "cart_item_add", body={"slug": "p", "quantity": 2})

        self.assertFalse(CartItem.objects.filter(cart__user=self.other).exists())

    def test_update_quantity(self):
        self._add_item(2)

        response = self.call("patch", "cart_item", {"slug": "p"}, body={"quantity": 5})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 5)
        self.assertEqual(CartItem.objects.get(cart__user=self.buyer).quantity, 5)

    def test_update_to_zero_removes_item(self):
        """数量 0 = 拿掉这一条 (与 service 的哨兵值一致), 回的是拿掉之后的整车."""
        self._add_item(2)

        response = self.call("patch", "cart_item", {"slug": "p"}, body={"quantity": 0})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"], [])
        self.assertFalse(CartItem.objects.filter(cart__user=self.buyer).exists())

    def test_update_unknown_slug_404(self):
        response = self.call("patch", "cart_item", {"slug": "p"}, body={"quantity": 1})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "cart_item_not_found")

    def test_update_other_users_item_404(self):
        """B 的车里有同一件商品 —— A 按 slug 改不到它 (归属在过滤条件里)."""
        CartItem.objects.create(
            cart=Cart.objects.create(user=self.other),
            product=self.product,
            quantity=3,
        )

        response = self.call("patch", "cart_item", {"slug": "p"}, body={"quantity": 1})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "cart_item_not_found")
        self.assertEqual(CartItem.objects.get(cart__user=self.other).quantity, 3)

    def test_remove_item(self):
        self._add_item(2)

        response = self.call("delete", "cart_item", {"slug": "p"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"], [])
        self.assertFalse(CartItem.objects.filter(cart__user=self.buyer).exists())

    def test_remove_unknown_slug_404(self):
        response = self.call("delete", "cart_item", {"slug": "p"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "cart_item_not_found")

    def test_clear_returns_empty_cart(self):
        self._add_item(2)

        response = self.call("delete", "cart_clear")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data, {"items": [], "total_count": 0, "total_amount": "0.00"}
        )

    def test_clear_does_not_touch_other_cart(self):
        self._add_item(1)
        CartItem.objects.create(
            cart=Cart.objects.create(user=self.other),
            product=self.product,
            quantity=3,
        )

        self.call("delete", "cart_clear")

        self.assertTrue(CartItem.objects.filter(cart__user=self.other).exists())

    def test_add_is_readable_immediately(self):
        """写后立刻可读: 加购完马上打 GET cart/ 就读得到 (写路径与读路径都不经缓存)."""
        self.call("post", "cart_item_add", body={"slug": "p", "quantity": 2})

        read = self.get("cart")
        self.assertEqual(read.data["total_count"], 2)
        self.assertEqual(read.data["items"][0]["product_slug"], "p")

    def test_missing_quantity_field_400_invalid_request(self):
        """请求体不合法是调用方契约问题, 与业务拒绝分开报码."""
        response = self.call("patch", "cart_item", {"slug": "p"}, body={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.code(response), "invalid_request")


class AgentOrderWriteTest(AgentWriteTestBase):
    """下单与取消."""

    def test_place_order_uses_default_address(self):
        self._add_item(2)

        response = self.call("post", "order_list", body={})

        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.data["order_no"], r"^\d+$")
        self.assertEqual(response.data["total_amount"], "20.00")
        self.assertEqual(response.data["status"], Order.Status.PENDING)
        self.assertEqual(
            response.data["shipping_address_snapshot"]["receiver_name"], "买家"
        )

        order = Order.objects.get(order_no=response.data["order_no"])
        self.assertEqual(order.user, self.buyer)
        self.assertFalse(CartItem.objects.filter(cart__user=self.buyer).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)

    def test_place_order_with_explicit_address(self):
        """传了 address_id 就用它, 不用默认地址."""
        second = ShippingAddress.objects.create(
            user=self.buyer,
            receiver_name="公司",
            phone="137",
            province="A",
            city="B",
            district="C",
            detail="F",
        )
        self._add_item(1)

        response = self.call("post", "order_list", body={"address_id": second.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["shipping_address_snapshot"]["receiver_name"], "公司"
        )

    def test_place_order_without_default_address_400(self):
        ShippingAddress.objects.filter(user=self.buyer).update(is_default=False)
        self._add_item(1)

        response = self.call("post", "order_list", body={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.code(response), "no_default_address")

    def test_place_order_unknown_address_404(self):
        self._add_item(1)

        response = self.call("post", "order_list", body={"address_id": 99999999})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "invalid_address")

    def test_place_order_other_users_address_404(self):
        """别人的地址等同于不存在 (不区分原因, 防枚举)."""
        self._add_item(1)

        response = self.call(
            "post", "order_list", body={"address_id": self.other_address.id}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "invalid_address")

    def test_place_order_empty_cart_409(self):
        response = self.call("post", "order_list", body={})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.code(response), "cart_empty")

    def test_place_order_insufficient_stock_409(self):
        self._add_item(3)
        Product.objects.filter(pk=self.product.pk).update(stock=1)

        response = self.call("post", "order_list", body={})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.code(response), "insufficient_stock")
        self.assertFalse(Order.objects.filter(user=self.buyer).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1, "整单回滚, 库存不该动")

    def test_place_order_reads_fresh_stock_not_cache(self):
        """库存判据取自数据库 —— 缓存里那份过期值不算数.

        先把网页接口的缓存捂热 (库存 10), 再绕过 signal 直接改库 (库存 1);
        下单若读了缓存就会放行, 真读到库里就拒绝.
        """
        with_cache = "/api/minimall/products/p/"
        self.assertEqual(self.client.get(with_cache).data["stock"], 10)
        # 清理交给 addCleanup: 断言失败也跑得到, 不留脏缓存给后面的用例
        self.addCleanup(cache.delete_pattern, "minimall:*")
        self._add_item(3)
        Product.objects.filter(pk=self.product.pk).update(stock=1)

        response = self.call("post", "order_list", body={})

        self.assertEqual(self.code(response), "insufficient_stock")
        self.assertEqual(
            self.client.get(with_cache).data["stock"], 10, "网页接口仍读缓存"
        )

    def test_place_order_is_readable_immediately(self):
        self._add_item(2)

        placed = self.call("post", "order_list", body={})

        listing = self.get("order_list")
        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(
            listing.data["results"][0]["order_no"], placed.data["order_no"]
        )
        detail = self.get("order_detail", {"order_no": placed.data["order_no"]})
        self.assertEqual(detail.data["total_amount"], "20.00")

    def test_cancel_paid_order_409(self):
        """付过款的订单取消不了 (2026-09-22 改判) —— 助手那条路照样被挡.

        判据从「订单处于什么状态」换成「买家说的是哪个动词」之后, `paid` 不再可取消:
        钱已经出去了, 要退就走退款申请. 状态 / 余额 / 库存一个都不许动.
        """
        order = self._paid_order()  # 20.00, 付款后余额 9980.00
        self.product.refresh_from_db()
        stock_before = self.product.stock

        response = self.call("post", "order_cancel", {"order_no": order.order_no})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.code(response), "invalid_order_status")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(self._balance(), Decimal("9980.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, stock_before)

    def test_cancel_unpaid_order_returns_zero_balance(self):
        """没付过款的单: 库存回滚, 退给余额的是 0.

        改判之后这是取消**唯一**的回执形状 (只有待付款的单能取消).
        """
        self._add_item(2)
        order = create_order(self.buyer, [CartItem.objects.get().id], self.address.id)

        response = self.call("post", "order_cancel", {"order_no": order.order_no})

        self.assertEqual(response.data["balance_returned"], "0.00")
        self.assertEqual(response.data["restocked_count"], 2)
        self.assertEqual(self._balance(), Decimal("10000.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10, "货还在仓库里, 库存加回去")

    def test_cancel_refunding_order_409(self):
        """退款中的订单不能取消 —— `refunding` 不在白名单里 (改判后只有 `pending`)."""
        order = self._paid_order()
        self.call("post", "refunds", body={"order_no": order.order_no})

        response = self.call("post", "order_cancel", {"order_no": order.order_no})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.code(response), "invalid_order_status")

    def test_cancel_other_users_order_404(self):
        item = CartItem.objects.create(
            cart=Cart.objects.create(user=self.other),
            product=self.product,
            quantity=1,
        )
        theirs = create_order(self.other, [item.id], self.other_address.id)

        response = self.call("post", "order_cancel", {"order_no": theirs.order_no})

        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Order.Status.PENDING)

    def test_cancel_unknown_order_404(self):
        response = self.call(
            "post", "order_cancel", {"order_no": "202601010000000000000000"}
        )

        self.assertEqual(response.status_code, 404)


class AgentOrderPayTest(AgentWriteTestBase):
    """代付 (POST orders/<no>/pay/) —— 请求体里只有一样东西: 买家的支付密码.

    这一片的边界 (issue 35): 密码**只在这一个请求体里**活着, 它不落库、不回显、
    不出现在任何一句话里. 判据全在 `pay_order` 那条锁内路径上, 端点只做三件事 ——
    认人, 取自己的单, 把 service 的异常翻成错误码.
    """

    def _unpaid_order(self, quantity: int = 2) -> Order:
        item = self._add_item(quantity)
        return create_order(self.buyer, [item.id], self.address.id)

    def pay(self, order_no, password: str = GOOD_PASSWORD, *, user=None):
        """打一次代付端点 (密码默认给对的那个 —— 只有一条用例故意给错).

        收订单**号**而不是订单对象: 有一条用例要打一个根本不存在的号;
        `user=False` 表示不带身份头 (与基类 `call` 同一个约定).
        """
        return self.call(
            "post",
            "order_pay",
            {"order_no": order_no},
            user=user,
            body={"payment_password": password},
        )

    def test_pay_with_the_right_password_marks_paid_and_returns_receipt(self):
        """带对密码 → 订单 `paid`、余额扣减, 回执答得出「付了多少、还剩多少」."""
        order = self._unpaid_order()

        response = self.pay(order.order_no)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "paid")
        self.assertEqual(response.data["status_display"], "已付款")
        self.assertEqual(response.data["total_amount"], "20.00")
        self.assertEqual(response.data["balance_remaining"], "9980.00")
        self.assertIsNotNone(response.data["paid_at"])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(self._balance(), Decimal("9980.00"))

    def test_wrong_password_is_a_distinguishable_code(self):
        """错密码 → `payment_failed`, **不是**兜底的 `order_rejected`, 钱一分不动.

        码分得开才有意义: 工具按码写文案 (「密码不对, 重新发起一次」与「余额不够,
        先充值」是两句不同的话). 顺带钉住密码不回显 —— 错误体里只有一句中文.
        """
        order = self._unpaid_order()

        response = self.pay(order.order_no, WRONG_PASSWORD)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.code(response), "payment_failed")
        self.assertNotEqual(self.code(response), "order_rejected")
        self.assertNotIn("000000", str(response.data), "错误体里不许回显密码")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(self._balance(), Decimal("10000.00"))

    def test_insufficient_balance_is_its_own_code(self):
        """余额不够 → `insufficient_balance` (与密码错分开 —— 下一步该做的事不同)."""
        Profile.objects.filter(user=self.buyer).update(balance=Decimal("10.00"))
        order = self._unpaid_order()

        response = self.pay(order.order_no)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.code(response), "insufficient_balance")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(self._balance(), Decimal("10.00"))

    def test_paying_a_paid_order_409(self):
        """已付款的单再付一次 → `invalid_order_status`, 而且**只扣了一次钱**.

        这条是「双击 / 重发会不会扣两次」在商城这一侧的兜底: 状态判在锁内, 第二次
        连密码都不会去校验.
        """
        order = self._paid_order()  # 20.00, 付款后余额 9980.00

        response = self.pay(order.order_no)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.code(response), "invalid_order_status")
        self.assertEqual(self._balance(), Decimal("9980.00"))

    def test_pay_other_users_order_404(self):
        """别人的订单一律当没有 (与只读那条同一条规矩: 不存在与不属于你不区分)."""
        item = CartItem.objects.create(
            cart=Cart.objects.create(user=self.other),
            product=self.product,
            quantity=1,
        )
        theirs = create_order(self.other, [item.id], self.other_address.id)

        response = self.pay(theirs.order_no)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "order_not_found")
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Order.Status.PENDING)

    def test_pay_unknown_order_404(self):
        response = self.pay("202601010000000000000000")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "order_not_found")

    def test_a_missing_password_is_an_invalid_request(self):
        """没带密码 → `invalid_request` (形状问题), 而不是「密码错」那个业务码."""
        order = self._unpaid_order()

        response = self.call("post", "order_pay", {"order_no": order.order_no}, body={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.code(response), "invalid_request")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)

    def test_a_password_of_the_wrong_length_is_an_invalid_request(self):
        """恰好 6 位 —— 长度不对在**端点**就被拦下, 不去烧一次 service 的失败路径.

        断言里带上「不回显」: 短密码是个短字符串, 它出现在错误体里的机会比长密码
        大得多 (DRF 的校验消息本身不带值, 这条守着它以后也别带).
        """
        order = self._unpaid_order()

        response = self.pay(order.order_no, "12345")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.code(response), "invalid_request")
        self.assertNotIn("12345", str(response.data))

    def test_missing_user_header_400(self):
        order = self._unpaid_order()

        response = self.pay(order.order_no, user=False)

        self.assertEqual(response.status_code, 400)


class AgentRefundWriteTest(AgentWriteTestBase):
    """申请退款与我的退款列表."""

    def test_request_refund_creates_request_without_amount(self):
        order = self._paid_order()

        response = self.call("post", "refunds", body={"order_no": order.order_no})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["order_no"], order.order_no)
        self.assertEqual(response.data["status"], RefundRequest.Status.REQUESTED)
        self.assertIsNone(response.data["amount"], "金额由管理员批准时协商")
        self.assertEqual(response.data["admin_note"], "")

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REFUNDING)

    def test_duplicate_request_409(self):
        order = self._paid_order()
        self.call("post", "refunds", body={"order_no": order.order_no})

        response = self.call("post", "refunds", body={"order_no": order.order_no})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.code(response), "refund_already_in_progress")

    def test_request_refund_on_unpaid_order_409(self):
        self._add_item(1)
        order = create_order(self.buyer, [CartItem.objects.get().id], self.address.id)

        response = self.call("post", "refunds", body={"order_no": order.order_no})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.code(response), "refund_not_allowed")

    def test_request_refund_on_other_users_order_404(self):
        item = CartItem.objects.create(
            cart=Cart.objects.create(user=self.other),
            product=self.product,
            quantity=1,
        )
        theirs = create_order(self.other, [item.id], self.other_address.id)

        response = self.call("post", "refunds", body={"order_no": theirs.order_no})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "order_not_found")
        self.assertFalse(RefundRequest.objects.filter(order=theirs).exists())

    def test_request_refund_unknown_order_404(self):
        response = self.call(
            "post", "refunds", body={"order_no": "202601010000000000000000"}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.code(response), "order_not_found")

    def test_list_carries_progress_and_admin_note(self):
        """故事 18 要的字段: 到哪一步了 + 为什么被驳回, 全在这条记录里."""
        order = self._paid_order()
        self.call("post", "refunds", body={"order_no": order.order_no})
        approve_refund(
            RefundRequest.objects.get(order=order),
            amount=Decimal("7.00"),
            note="协商一致退 7 元",
        )

        response = self.get("refunds")

        self.assertEqual(len(response.data), 1)
        row = response.data[0]
        self.assertEqual(row["order_no"], order.order_no)
        self.assertEqual(row["status"], RefundRequest.Status.APPROVED)
        self.assertEqual(row["amount"], "7.00")
        self.assertEqual(row["admin_note"], "协商一致退 7 元")
        self.assertIsNotNone(row["approved_at"])
        self.assertIsNone(row["refunded_at"])

    def test_list_only_own_refunds(self):
        theirs_item = CartItem.objects.create(
            cart=Cart.objects.create(user=self.other),
            product=self.product,
            quantity=1,
        )
        theirs = create_order(self.other, [theirs_item.id], self.other_address.id)
        # 别人的订单假装已付款 (只搭台, 不走 service —— 它要支付密码)
        Order.objects.filter(pk=theirs.pk).update(status=Order.Status.PAID)
        RefundRequest.objects.create(
            order=theirs,
            status=RefundRequest.Status.REQUESTED,
            order_status_before=Order.Status.PAID,
        )

        response = self.get("refunds")

        self.assertEqual(response.data, [])
        self.assertEqual(RefundRequest.objects.count(), 1)

    def test_list_is_not_paginated(self):
        """退款单天然少, 列全即可 —— 这是本组端点唯一不分页的列表."""
        self._add_item(1)
        order = create_order(self.buyer, [CartItem.objects.get().id], self.address.id)
        RefundRequest.objects.create(
            order=order,
            status=RefundRequest.Status.REJECTED,
            order_status_before=Order.Status.PENDING,
        )

        response = self.get("refunds")

        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)

    def test_request_is_readable_immediately(self):
        order = self._paid_order()

        self.call("post", "refunds", body={"order_no": order.order_no})

        self.assertEqual(len(self.get("refunds").data), 1)

    # --- 身份头 (与 issue 02 同一套拒绝) ----------------------------------

    def test_missing_user_header_400(self):
        response = self.call("post", "refunds", user=False, body={"order_no": "1"})

        self.assertEqual(response.status_code, 400)


class AgentErrorMappingTest(SimpleTestCase):
    """错误码表本身 —— 它是 issue 12 那个工具层的契约."""

    def test_every_service_exception_has_its_own_code(self):
        """services.py 里每个业务异常都要登记 —— 漏一个就退化成兜底码.

        遍历子类而不是手抄清单 —— 以后新加异常忘了登记, 这条用例会红.
        """
        missing = []
        for exc_class in self._all_subclasses(OrderServiceError):
            if exc_class not in EXCEPTION_CODES:
                missing.append(exc_class.__name__)
        self.assertEqual(missing, [], f"这些业务异常没有错误码: {missing}")

    def test_every_code_has_status_and_message(self):
        for exc_class, code in EXCEPTION_CODES.items():
            with self.subTest(exception=exc_class.__name__, code=code):
                self.assertIn(code, ERROR_CODES)
                spec = ERROR_CODES[code]
                self.assertIn(spec.status, (400, 404, 409))
                self.assertTrue(spec.message.strip())

    def test_codes_are_distinct(self):
        """一个码只给一件事 —— 混码正是这条清单要避免的."""
        codes = list(EXCEPTION_CODES.values())
        self.assertEqual(len(codes), len(set(codes)))

    def test_unreachable_but_required_codes_exist(self):
        """金额越界 / 余额不足: 码要在.

        「余额不足」到 issue 35 有了入口 (代付端点, 见 `AgentOrderPayTest`) —— 它
        的用例与这一条不重复: 那条断的是「真触发时码对不对」, 这条断的是「这张表
        里有没有它」(工具层按码写文案, 缺了就说不清下一步).

        **金额越界仍然没有入口**: 退款金额由管理员批准时定 (`request_refund` 不带
        金额), 助手那条路上没有哪一步会传一个金额进来. 这是它现在唯一的意义 ——
        留着是因为删掉它等于把「这个码缺席」这件事从表里抹掉.
        """
        self.assertEqual(ERROR_CODES["invalid_refund_amount"].status, 400)
        self.assertEqual(ERROR_CODES["insufficient_balance"].status, 400)

    @staticmethod
    def _all_subclasses(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from AgentErrorMappingTest._all_subclasses(sub)
