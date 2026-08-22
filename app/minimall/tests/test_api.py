"""API endpoint tests."""

import contextlib

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from app.minimall.models import (
    Cart,
    CartItem,
    Category,
    Product,
    Profile,
    ShippingAddress,
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
