"""Service layer tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from minimall.models import Cart, CartItem, Category, Order, Product, Profile, ShippingAddress
from minimall.services import (
    InsufficientStockError,
    InvalidOrderStatusError,
    PaymentError,
    cancel_order,
    create_order,
    pay_order,
    receive_order,
    ship_order,
)

User = get_user_model()


class OrderServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="svc", email="svc@t.com", password="pass")
        Profile.objects.create(user=self.user)
        self.user.minimall_profile.set_payment_password("123456")
        self.user.minimall_profile.save()
        self.cat = Category.objects.create(name="Test", slug="svc-test")
        self.prod = Product.objects.create(
            name="P", slug="p", category=self.cat, price=10.00, stock=20
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

    def _cart_item(self, qty=3):
        cart = Cart.objects.get_or_create(user=self.user)[0]
        return CartItem.objects.create(cart=cart, product=self.prod, quantity=qty)

    def test_create_order_deducts_stock(self):
        ci = self._cart_item(3)
        order = create_order(self.user, [ci.id], self.addr.id)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, 17)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertFalse(CartItem.objects.filter(id=ci.id).exists())

    def test_create_order_insufficient_stock(self):
        ci = self._cart_item(100)
        with self.assertRaises(InsufficientStockError):
            create_order(self.user, [ci.id], self.addr.id)

    def test_pay_wrong_password(self):
        ci = self._cart_item(1)
        order = create_order(self.user, [ci.id], self.addr.id)
        with self.assertRaises(PaymentError):
            pay_order(order, "wrong")

    def test_pay_success(self):
        ci = self._cart_item(1)
        order = create_order(self.user, [ci.id], self.addr.id)
        pay_order(order, "123456")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertIsNotNone(order.paid_at)

    def test_cancel_restores_stock(self):
        ci = self._cart_item(5)
        order = create_order(self.user, [ci.id], self.addr.id)
        self.prod.refresh_from_db()
        stock_after_order = self.prod.stock
        cancel_order(order)
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.stock, stock_after_order + 5)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_cannot_cancel_shipped(self):
        ci = self._cart_item(1)
        order = create_order(self.user, [ci.id], self.addr.id)
        pay_order(order, "123456")
        order.refresh_from_db()
        ship_order(order)
        order.refresh_from_db()
        with self.assertRaises(InvalidOrderStatusError):
            cancel_order(order)

    def test_full_flow(self):
        ci = self._cart_item(2)
        order = create_order(self.user, [ci.id], self.addr.id)
        self.assertEqual(order.status, "pending")
        pay_order(order, "123456")
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        ship_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")
        receive_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, "received")
