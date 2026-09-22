"""API endpoint tests."""

import contextlib
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
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
    MAX_CART_ITEM_QUANTITY,
    approve_refund,
    reject_refund,
    settle_refund,
)

User = get_user_model()


def _clear_minimall_cache():
    """每个测试前清空 minimall 缓存, 避免跨测试共享真实 Redis 导致数据污染."""
    with contextlib.suppress(Exception):
        cache.delete_pattern("minimall:*")


class ProductAPITest(TestCase):
    def setUp(self):
        _clear_minimall_cache()
        self.client = APIClient()
        self.cat = Category.objects.create(name="Electronics", slug="electronics")
        self.subcat = Category.objects.create(
            name="Phones", slug="phones", parent=self.cat
        )
        self.p1 = Product.objects.create(
            name="iPhone", slug="iphone", category=self.subcat, price=999.00, stock=10
        )
        self.p2 = Product.objects.create(
            name="iPad", slug="ipad", category=self.cat, price=599.00, stock=5
        )
        self.p3 = Product.objects.create(
            name="Hidden",
            slug="hidden",
            category=self.cat,
            price=10.00,
            stock=1,
            is_active=False,
        )

    def test_list_products(self):
        r = self.client.get("/api/minimall/products/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 2)  # p3 is inactive

    def test_search(self):
        r = self.client.get("/api/minimall/products/?search=iPhone")
        self.assertEqual(r.data["count"], 1)

    def test_category_filter_includes_children(self):
        r = self.client.get("/api/minimall/products/?category=electronics")
        self.assertEqual(r.data["count"], 2)

    def test_price_filter(self):
        r = self.client.get("/api/minimall/products/?min_price=600")
        self.assertEqual(r.data["count"], 1)  # only iPhone 999

    def test_ordering(self):
        r = self.client.get("/api/minimall/products/?ordering=price")
        self.assertEqual(r.data["results"][0]["name"], "iPad")

    def test_inactive_not_shown(self):
        r = self.client.get("/api/minimall/products/?search=Hidden")
        self.assertEqual(r.data["count"], 0)

    def test_category_tree(self):
        r = self.client.get("/api/minimall/categories/")
        self.assertEqual(len(r.data), 1)
        self.assertEqual(len(r.data[0]["children"]), 1)

    def test_product_detail(self):
        r = self.client.get("/api/minimall/products/iphone/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["name"], "iPhone")


class CartAPITest(TestCase):
    def setUp(self):
        _clear_minimall_cache()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="cartuser", email="cu@t.com", password="pass"
        )
        self.other = User.objects.create_user(
            username="other", email="ou@t.com", password="pass"
        )
        self.cat = Category.objects.create(name="Test", slug="test")
        self.prod = Product.objects.create(
            name="P", slug="p", category=self.cat, price=10.00, stock=5
        )
        self.client.force_login(self.user)

    def test_empty_cart(self):
        r = self.client.get("/api/minimall/cart/")
        self.assertEqual(r.data["total_count"], 0)

    def test_add_item(self):
        r = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 2},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["quantity"], 2)

    def test_repeat_add_accumulates(self):
        self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 2},
            format="json",
        )
        r = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 1},
            format="json",
        )
        self.assertEqual(r.data["quantity"], 3)

    def test_items_exceed_stock(self):
        r = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 10},
            format="json",
        )
        self.assertEqual(r.data["quantity"], 5)  # capped at stock

    def test_add_out_of_stock(self):
        self.prod.stock = 0
        self.prod.save()
        r = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 1},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertFalse(CartItem.objects.filter(cart__user=self.user).exists())

    def test_unauthorized(self):
        self.client.logout()
        r = self.client.get("/api/minimall/cart/")
        self.assertEqual(r.status_code, 403)

    def test_other_user_cannot_access(self):
        item = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 1},
            format="json",
        ).data
        self.client.logout()
        self.client.force_login(self.other)
        r = self.client.patch(
            f"/api/minimall/cart/items/{item['id']}/", {"quantity": 5}, format="json"
        )
        self.assertEqual(r.status_code, 404)

    def test_update_quantity(self):
        item = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 2},
            format="json",
        ).data
        r = self.client.patch(
            f"/api/minimall/cart/items/{item['id']}/", {"quantity": 3}, format="json"
        )
        self.assertEqual(r.data["quantity"], 3)

    def test_delete_item(self):
        item = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 1},
            format="json",
        ).data
        r = self.client.delete(f"/api/minimall/cart/items/{item['id']}/delete/")
        self.assertEqual(r.status_code, 204)

    def test_add_capped_at_max_quantity(self):
        """库存再多也封顶在单件上限 (上限是业务规则, 不只是库存的衍生)."""
        big = Product.objects.create(
            name="Big", slug="big", category=self.cat, price=10.00, stock=1000
        )
        r = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": big.id, "quantity": MAX_CART_ITEM_QUANTITY + 1},
            format="json",
        )
        self.assertEqual(r.data["quantity"], MAX_CART_ITEM_QUANTITY)

    def test_update_quantity_zero_removes_item(self):
        item = self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 1},
            format="json",
        ).data
        r = self.client.patch(
            f"/api/minimall/cart/items/{item['id']}/", {"quantity": 0}, format="json"
        )
        self.assertEqual(r.status_code, 204)
        self.assertFalse(CartItem.objects.filter(id=item["id"]).exists())

    def test_clear_cart(self):
        self.client.post(
            "/api/minimall/cart/items/",
            {"product_id": self.prod.id, "quantity": 2},
            format="json",
        )
        r = self.client.delete("/api/minimall/cart/clear/")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(CartItem.objects.filter(cart__user=self.user).count(), 0)


class OrderAPITest(TestCase):
    def setUp(self):
        _clear_minimall_cache()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="orderuser", email="ou@t.com", password="pass"
        )
        Profile.objects.create(user=self.user, balance=10000)
        self.user.minimall_profile.set_payment_password("123456")
        self.user.minimall_profile.save()
        self.cat = Category.objects.create(name="Test", slug="test")
        self.prod = Product.objects.create(
            name="P", slug="p", category=self.cat, price=10.00, stock=10
        )
        self.addr = ShippingAddress.objects.create(
            user=self.user,
            receiver_name="X",
            phone="1",
            province="A",
            city="B",
            district="C",
            detail="D",
        )
        self.client.force_login(self.user)

    def _setup_cart(self, quantity=2):
        cart = Cart.objects.get_or_create(user=self.user)[0]
        item = CartItem.objects.create(cart=cart, product=self.prod, quantity=quantity)
        return [item.id]

    def test_create_order(self):
        item_ids = self._setup_cart(2)
        r = self.client.post(
            "/api/minimall/orders/",
            {"cart_item_ids": item_ids, "address_id": self.addr.id},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 8)

    def test_order_insufficient_stock(self):
        item_ids = self._setup_cart(20)
        r = self.client.post(
            "/api/minimall/orders/",
            {"cart_item_ids": item_ids, "address_id": self.addr.id},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_pay_order(self):
        item_ids = self._setup_cart(1)
        order_data = self.client.post(
            "/api/minimall/orders/",
            {"cart_item_ids": item_ids, "address_id": self.addr.id},
            format="json",
        ).data
        r = self.client.post(
            f"/api/minimall/orders/{order_data['order_no']}/pay/",
            {"payment_password": "123456"},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "paid")

    def test_pay_wrong_password(self):
        item_ids = self._setup_cart(1)
        order_data = self.client.post(
            "/api/minimall/orders/",
            {"cart_item_ids": item_ids, "address_id": self.addr.id},
            format="json",
        ).data
        r = self.client.post(
            f"/api/minimall/orders/{order_data['order_no']}/pay/",
            {"payment_password": "wrong"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cancel_order_restores_stock(self):
        item_ids = self._setup_cart(3)
        order_data = self.client.post(
            "/api/minimall/orders/",
            {"cart_item_ids": item_ids, "address_id": self.addr.id},
            format="json",
        ).data
        self.prod.refresh_from_db()
        stock_before = self.prod.stock
        r = self.client.post(f"/api/minimall/orders/{order_data['order_no']}/cancel/")
        self.assertEqual(r.status_code, 200)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, stock_before + 3)

    def test_other_user_cannot_access_order(self):
        item_ids = self._setup_cart(1)
        order_data = self.client.post(
            "/api/minimall/orders/",
            {"cart_item_ids": item_ids, "address_id": self.addr.id},
            format="json",
        ).data
        other = User.objects.create_user(
            username="o2", email="o2@t.com", password="pass"
        )
        self.client.logout()
        self.client.force_login(other)
        r = self.client.get(f"/api/minimall/orders/{order_data['order_no']}/")
        self.assertEqual(r.status_code, 404)


class OrderRefundAPITest(TestCase):
    """买家退款入口 (故事 17 申请 / 故事 18 看进度).

    订单状态与退款单状态是同一件事的两面 (ADR-0004), 所以这里不只断言端点回了什么,
    还断言**订单**跟着变了 —— 页面就是靠订单状态决定露不露那个按钮的.
    """

    def setUp(self):
        _clear_minimall_cache()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="refunduser", email="ru@t.com", password="pass"
        )
        Profile.objects.create(user=self.user, balance=10000)
        self.user.minimall_profile.set_payment_password("123456")
        self.user.minimall_profile.save()
        self.cat = Category.objects.create(name="Test", slug="test")
        self.prod = Product.objects.create(
            name="P", slug="p", category=self.cat, price=10.00, stock=10
        )
        self.addr = ShippingAddress.objects.create(
            user=self.user,
            receiver_name="X",
            phone="1",
            province="A",
            city="B",
            district="C",
            detail="D",
        )
        self.client.force_login(self.user)
        self.order = self._paid_order()

    def _place_order(self, quantity=2):
        cart = Cart.objects.get_or_create(user=self.user)[0]
        item = CartItem.objects.create(cart=cart, product=self.prod, quantity=quantity)
        return self.client.post(
            "/api/minimall/orders/",
            {"cart_item_ids": [item.id], "address_id": self.addr.id},
            format="json",
        ).data["order_no"]

    def _paid_order(self, quantity=2):
        """退款的起点是"钱已经出去"的订单, 所以先下一单再付掉."""
        order_no = self._place_order(quantity)
        self.client.post(
            f"/api/minimall/orders/{order_no}/pay/",
            {"payment_password": "123456"},
            format="json",
        )
        return Order.objects.get(order_no=order_no)

    def _refund_url(self, order=None):
        order = order or self.order
        return f"/api/minimall/orders/{order.order_no}/refund/"

    def _detail(self, order=None):
        order = order or self.order
        return self.client.get(f"/api/minimall/orders/{order.order_no}/").data

    def _active_count(self):
        return self.client.get("/api/minimall/orders/active-count/").data["count"]

    def test_request_refund_marks_order_refunding(self):
        # 起点是 `paid` —— 2026-09-22 改判之后它正是「该走退款」的那个状态: 付过款
        # 就不能取消了, 页面与端点都把 `paid` 算进可退款 (同一份 REFUNDABLE_STATUSES).
        r = self.client.post(self._refund_url())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "refunding")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.REFUNDING)
        refund = RefundRequest.objects.get(order=self.order)
        self.assertEqual(refund.status, RefundRequest.Status.REQUESTED)
        self.assertEqual(refund.order_status_before, Order.Status.PAID)
        # 金额这一步还是不填的 —— 它由管理员批准时协商
        self.assertIsNone(refund.amount)

    def test_request_refund_does_not_restore_stock(self):
        """**申请**这一步不动库存 —— 回滚落在打款那一步 (判据见服务层用例).

        申请时货还被这一单占着 (订单是 `refunding`), 提前放回库存等于让同一单同时
        占着货和钱; 打款时才按「申请前发没发货」决定回不回滚.
        """
        self.prod.refresh_from_db()
        stock_before = self.prod.stock
        self.client.post(self._refund_url())
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, stock_before)

    def test_detail_has_no_refund_before_any_request(self):
        self.assertIsNone(self._detail()["refund"])

    def test_detail_reports_requested_refund(self):
        self.client.post(self._refund_url())
        refund = self._detail()["refund"]
        self.assertEqual(refund["status"], "requested")
        self.assertIsNone(refund["amount"])

    def test_detail_reports_rejected_reason(self):
        self.client.post(self._refund_url())
        reject_refund(RefundRequest.objects.get(order=self.order), note="商品已发出")

        detail = self._detail()
        # 驳回把订单恢复成申请前的状态 —— 页面据此重新露出"申请退款"按钮
        self.assertEqual(detail["status"], Order.Status.PAID)
        self.assertEqual(detail["refund"]["status"], "rejected")
        self.assertEqual(detail["refund"]["admin_note"], "商品已发出")

    def test_rejected_refund_can_be_requested_again(self):
        self.client.post(self._refund_url())
        reject_refund(RefundRequest.objects.get(order=self.order), note="再想想")
        self.assertEqual(self.client.post(self._refund_url()).status_code, 200)
        self.assertEqual(RefundRequest.objects.filter(order=self.order).count(), 2)

    def test_detail_reports_settled_amount(self):
        self.client.post(self._refund_url())
        refund = RefundRequest.objects.get(order=self.order)
        approve_refund(refund, amount=Decimal("15.00"), note="协商一致")
        settle_refund(refund)

        detail = self._detail()
        self.assertEqual(detail["status"], Order.Status.REFUNDED)
        self.assertEqual(detail["refund"]["status"], "refunded")
        self.assertEqual(detail["refund"]["amount"], "15.00")
        self.assertIsNotNone(detail["refunded_at"])

    def test_pending_order_cannot_refund(self):
        order_no = self._place_order(1)  # 只下单, 不付款
        r = self.client.post(f"/api/minimall/orders/{order_no}/refund/")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(
            RefundRequest.objects.filter(order__order_no=order_no).exists()
        )

    def test_duplicate_request_rejected(self):
        self.client.post(self._refund_url())
        r = self.client.post(self._refund_url())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(RefundRequest.objects.filter(order=self.order).count(), 1)

    def test_refunding_order_still_counts_as_active(self):
        """退款中的订单仍算「进行中」—— 一申请就少一个角标, 看着像订单没了."""
        self.assertEqual(self._active_count(), 1)  # paid 阶段
        self.client.post(self._refund_url())
        self.assertEqual(self._active_count(), 1)  # refunding 阶段, 不是 0

    def test_unauthorized(self):
        self.client.logout()
        r = self.client.post(self._refund_url())
        self.assertEqual(r.status_code, 403)

    def test_other_user_cannot_refund(self):
        other = User.objects.create_user(
            username="o3", email="o3@t.com", password="pass"
        )
        self.client.logout()
        self.client.force_login(other)
        r = self.client.post(self._refund_url())
        self.assertEqual(r.status_code, 404)
        self.assertFalse(RefundRequest.objects.filter(order=self.order).exists())
