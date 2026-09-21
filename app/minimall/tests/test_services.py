"""Service layer tests."""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

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
from app.minimall.services import (
    MAX_CART_ITEM_QUANTITY,
    ORDER_NO_MAX_ATTEMPTS,
    CartItemNotFoundError,
    InsufficientStockError,
    InvalidOrderStatusError,
    OrderNumberConflictError,
    OutOfStockError,
    PaymentError,
    ProductUnavailableError,
    add_to_cart,
    cancel_order,
    clear_cart,
    create_order,
    pay_order,
    receive_order,
    remove_cart_item,
    ship_order,
    update_cart_item,
)

User = get_user_model()


class OrderServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="svc", email="svc@t.com", password="pass"
        )
        Profile.objects.create(user=self.user, balance=10000)
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

    def _taken_order_no(self, order_no: str) -> None:
        """先占掉一个订单号 (用来逼出撞车); 具体值不重要, 只要它已经存在."""
        Order.objects.create(
            order_no=order_no,
            user=self.user,
            status=Order.Status.PENDING,
            total_amount=Decimal("1.00"),
            shipping_address_snapshot={},
        )

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

    # ------------------------------------------------------------------
    # 判据一律取**锁内 re-fetch** 的值, 不用调用方传进来的实例
    # ------------------------------------------------------------------
    # 传进来的实例是几秒前读的, 期间订单可能已经被另一个请求推动了. 下面每条
    # 都用「另一个实例把 DB 推进一步」制造出真实的陈旧实例 (不是改字段模拟的).

    def test_pay_twice_with_stale_instance_deducts_once(self):
        """两个「待付款」实例各付一次 → 第二次失败, 且余额只扣一次.

        真实场景是买家双击支付: 两个请求在同一刻各自读到「待付款」的订单.
        第二次必须被锁内 re-fetch 出来的真实状态挡下 (老实现看的是手里那份
        实例, 于是钱被扣了两遍).
        """
        ci = self._cart_item(1)
        order = create_order(self.user, [ci.id], self.addr.id)
        first = Order.objects.get(pk=order.pk)
        stale = Order.objects.get(pk=order.pk)  # 双击里的第二个请求读到的实例

        pay_order(first, "123456")
        with self.assertRaises(InvalidOrderStatusError):
            pay_order(stale, "123456")

        self.assertEqual(
            Profile.objects.get(pk=self.user.minimall_profile.pk).balance,
            Decimal("9990.00"),
        )
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.PAID)

    def test_cancel_with_stale_instance_rejects_shipped(self):
        """陈旧实例取消一张**已发货**订单 → 拒绝, 不退钱也不回滚库存.

        老实现读的是传入实例的 status (它看到的还是 paid) —— 于是钱退了, 库存
        也加回去了, 而货其实已经发出去了.
        """
        ci = self._cart_item(2)
        order = create_order(self.user, [ci.id], self.addr.id)
        pay_order(order, "123456")
        stale = Order.objects.get(pk=order.pk)  # 这一刻它还是 paid
        ship_order(Order.objects.get(pk=order.pk))
        stock = Product.objects.get(pk=self.prod.pk).stock
        balance = Profile.objects.get(pk=self.user.minimall_profile.pk).balance

        with self.assertRaises(InvalidOrderStatusError):
            cancel_order(stale)

        self.assertEqual(Product.objects.get(pk=self.prod.pk).stock, stock)
        self.assertEqual(
            Profile.objects.get(pk=self.user.minimall_profile.pk).balance, balance
        )
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.SHIPPED)

    def test_cancel_rejected_for_refunding_order(self):
        """退款中的订单自动不可取消: 取消的白名单只有 pending / paid.

        `refunding` 目前还不是 Order.Status 的枚举值 (issue 10 才加), 这里用
        字面量钉住这条约束 —— 枚举加上之后, 这条仍然成立.
        """
        ci = self._cart_item(1)
        order = create_order(self.user, [ci.id], self.addr.id)
        pay_order(order, "123456")
        refunding = Order.objects.get(pk=order.pk)
        refunding.status = "refunding"
        refunding.save(update_fields=["status"])

        with self.assertRaises(InvalidOrderStatusError):
            cancel_order(Order.objects.get(pk=order.pk))

    def test_create_order_snapshot_uses_locked_price(self):
        """事务外那次读与进锁之间改了价 → 下单按**锁内**的价格快照.

        并发改价没法在单线程里稳定复现, 于是把改价卡在窗口正中 (事务外那次读
        之后, 进锁之前): 那次读只用来算要锁哪些商品, 价格必须来自加锁后的
        product_map. 老实现取的是事务外 select_related 带出来的 product 实例
        (改价前的旧价), 所以这条用例在它上面是红的.
        """
        ci = self._cart_item(2)
        real_get = ShippingAddress.objects.get

        def change_price_then_get(*args, **kwargs):
            Product.objects.filter(pk=self.prod.pk).update(price=Decimal("20.00"))
            return real_get(*args, **kwargs)

        with mock.patch.object(
            ShippingAddress.objects, "get", side_effect=change_price_then_get
        ):
            order = create_order(self.user, [ci.id], self.addr.id)

        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.product_price, Decimal("20.00"))
        self.assertEqual(order.total_amount, Decimal("40.00"))

    def test_create_order_retries_when_order_no_collides(self):
        """订单号撞车 → 换一个号重试, 别把 IntegrityError 冒给买家.

        撞车靠**喂号**制造 (patch 掉生成器): 真等一次随机撞车要跑上万单, 而这里
        要验的是「撞了以后怎么办」. 前几次都喂成已被占用的号, 最后一次喂一个没
        人用过的 —— 于是重试必须成功, 且拿到的正是最后那个号.
        """
        ci = self._cart_item(1)
        taken = "202609211200001234567890"
        self._taken_order_no(taken)
        fresh = "202609211200009999999999"

        with mock.patch(
            "app.minimall.services.generate_order_no",
            side_effect=[taken] * (ORDER_NO_MAX_ATTEMPTS - 1) + [fresh],
        ):
            order = create_order(self.user, [ci.id], self.addr.id)

        self.assertEqual(order.order_no, fresh)

    def test_create_order_raises_when_order_no_keeps_colliding(self):
        """一直撞车 → 抛业务异常, 并且整单回滚 (库存与购物车行原样)."""
        ci = self._cart_item(1)
        taken = "202609211200001234567890"
        self._taken_order_no(taken)

        with (
            mock.patch("app.minimall.services.generate_order_no", return_value=taken),
            self.assertRaises(OrderNumberConflictError),
        ):
            create_order(self.user, [ci.id], self.addr.id)

        self.assertEqual(Product.objects.get(pk=self.prod.pk).stock, 20)
        self.assertTrue(CartItem.objects.filter(id=ci.id).exists())


class CartServiceTest(TestCase):
    """购物车写操作 (页面与助手共用的那一份) —— 含数量上限."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="cart", email="cart@t.com", password="pass"
        )
        self.other = User.objects.create_user(
            username="other-cart", email="oc@t.com", password="pass"
        )
        self.cat = Category.objects.create(name="Test", slug="cart-svc")
        self.prod = Product.objects.create(
            name="P", slug="p", category=self.cat, price=10.00, stock=1000
        )

    def test_add_creates_then_accumulates(self):
        item, created = add_to_cart(self.user, product_id=self.prod.id, quantity=2)
        self.assertTrue(created)
        self.assertEqual(item.quantity, 2)

        again, created = add_to_cart(self.user, product_id=self.prod.id, quantity=1)
        self.assertFalse(created)
        self.assertEqual(again.pk, item.pk)
        self.assertEqual(again.quantity, 3)

    def test_add_caps_at_stock(self):
        """截断到库存 (与页面从前一致: 要 10 件而库存 5 件, 拿到 5 件)."""
        self.prod.stock = 5
        self.prod.save(update_fields=["stock"])

        item, _ = add_to_cart(self.user, product_id=self.prod.id, quantity=10)

        self.assertEqual(item.quantity, 5)

    def test_add_caps_at_max_quantity(self):
        """库存充足时截断到单件上限 —— 库存之外的第二道闸."""
        item, _ = add_to_cart(
            self.user, product_id=self.prod.id, quantity=MAX_CART_ITEM_QUANTITY + 1
        )

        self.assertEqual(item.quantity, MAX_CART_ITEM_QUANTITY)

    def test_add_caps_at_max_quantity_when_accumulating(self):
        """累加也要截断: 分两次加购不能绕过上限."""
        add_to_cart(self.user, product_id=self.prod.id, quantity=MAX_CART_ITEM_QUANTITY)
        item, _ = add_to_cart(self.user, product_id=self.prod.id, quantity=5)

        self.assertEqual(item.quantity, MAX_CART_ITEM_QUANTITY)

    def test_add_rejects_out_of_stock(self):
        self.prod.stock = 0
        self.prod.save(update_fields=["stock"])

        with self.assertRaises(OutOfStockError):
            add_to_cart(self.user, product_id=self.prod.id, quantity=1)

        self.assertFalse(CartItem.objects.filter(cart__user=self.user).exists())

    def test_add_rejects_unavailable_product(self):
        self.prod.is_active = False
        self.prod.save(update_fields=["is_active"])

        with self.assertRaises(ProductUnavailableError):
            add_to_cart(self.user, product_id=self.prod.id, quantity=1)

    def test_update_caps_at_max_quantity(self):
        item, _ = add_to_cart(self.user, product_id=self.prod.id, quantity=1)

        updated = update_cart_item(
            self.user, cart_item_id=item.id, quantity=MAX_CART_ITEM_QUANTITY + 400
        )

        self.assertEqual(updated.quantity, MAX_CART_ITEM_QUANTITY)

    def test_update_to_zero_removes_item(self):
        item, _ = add_to_cart(self.user, product_id=self.prod.id, quantity=1)

        self.assertIsNone(update_cart_item(self.user, cart_item_id=item.id, quantity=0))
        self.assertFalse(CartItem.objects.filter(id=item.id).exists())

    def test_update_other_users_item_not_found(self):
        """别人的条目一律当成不存在 (归属校验在 service 里, 两个入口都受益)."""
        item, _ = add_to_cart(self.user, product_id=self.prod.id, quantity=1)

        with self.assertRaises(CartItemNotFoundError):
            update_cart_item(self.other, cart_item_id=item.id, quantity=5)
        with self.assertRaises(CartItemNotFoundError):
            remove_cart_item(self.other, cart_item_id=item.id)

    def test_remove_deletes_only_that_item(self):
        item, _ = add_to_cart(self.user, product_id=self.prod.id, quantity=1)

        remove_cart_item(self.user, cart_item_id=item.id)

        self.assertFalse(CartItem.objects.filter(id=item.id).exists())
        with self.assertRaises(CartItemNotFoundError):
            remove_cart_item(self.user, cart_item_id=item.id)

    def test_clear_removes_only_my_items(self):
        mine, _ = add_to_cart(self.user, product_id=self.prod.id, quantity=1)
        theirs, _ = add_to_cart(self.other, product_id=self.prod.id, quantity=1)

        clear_cart(self.user)

        self.assertFalse(CartItem.objects.filter(id=mine.id).exists())
        self.assertTrue(CartItem.objects.filter(id=theirs.id).exists())
