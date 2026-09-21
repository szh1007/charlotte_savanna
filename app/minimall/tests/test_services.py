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
    RefundRequest,
    ShippingAddress,
)
from app.minimall.services import (
    MAX_CART_ITEM_QUANTITY,
    ORDER_NO_MAX_ATTEMPTS,
    CartItemNotFoundError,
    InsufficientBalanceError,
    InsufficientStockError,
    InvalidOrderStatusError,
    InvalidRefundAmountError,
    InvalidRefundStatusError,
    OrderNumberConflictError,
    OutOfStockError,
    PaymentError,
    ProductUnavailableError,
    RefundAlreadyInProgressError,
    RefundNotAllowedError,
    add_to_cart,
    approve_refund,
    cancel_order,
    clear_cart,
    complete_order,
    create_order,
    pay_order,
    receive_order,
    reject_refund,
    remove_cart_item,
    request_refund,
    settle_refund,
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

    def test_pay_insufficient_balance(self):
        """余额不够: 与「密码错了」是两个异常 —— 一个充值能解决, 一个要重输.

        (余额不足这个错误码在 agent 端点上暂时触发不到: 支付走 L3 的挂起.
        但异常分开是 agent 那层能把它讲成不同中文的前提.)
        """
        self.user.minimall_profile.balance = Decimal("1.00")
        self.user.minimall_profile.save(update_fields=["balance"])
        ci = self._cart_item(1)
        order = create_order(self.user, [ci.id], self.addr.id)

        with self.assertRaises(InsufficientBalanceError):
            pay_order(order, "123456")

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING, "没扣钱也没推状态")

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

        09 写这条时 `refunding` 还不是枚举值, 用的是字面量; 10 加上枚举后改成
        枚举 (值没变, 断言照旧成立).
        """
        ci = self._cart_item(1)
        order = create_order(self.user, [ci.id], self.addr.id)
        pay_order(order, "123456")
        refunding = Order.objects.get(pk=order.pk)
        refunding.status = Order.Status.REFUNDING
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


class RefundServiceTest(TestCase):
    """退款域: 四个转换点, 两个状态机的耦合 (ADR-0004 的核心风险).

    每条转换都断言**两张表一起对**: `RefundRequest.status` 与 `Order.status`.
    只测其中一张, 漏掉的那种不一致就测不出来 —— 而这个耦合是有意选的.
    """

    # 退款只能从「钱已经出去」之后发起 (pending 走取消, 不走退款).
    # 目标状态 → 推到它需要的几步; 发货 / 收货 / 完成 都只推一步状态.
    _PUSH_TO = {
        Order.Status.PAID: (),
        Order.Status.SHIPPED: (ship_order,),
        Order.Status.RECEIVED: (ship_order, receive_order),
        Order.Status.COMPLETED: (ship_order, receive_order, complete_order),
    }

    def setUp(self):
        self.user = User.objects.create_user(
            username="refund-svc", email="rf@t.com", password="pass"
        )
        Profile.objects.create(user=self.user, balance=10000)
        self.user.minimall_profile.set_payment_password("123456")
        self.user.minimall_profile.save()
        self.cat = Category.objects.create(name="Refund", slug="refund-svc")
        self.prod = Product.objects.create(
            name="P", slug="p-refund", category=self.cat, price=100.00, stock=20
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

    def _paid_order(self, status=Order.Status.PAID, quantity=1):
        """一张已付款订单, 按需推进到 shipped / received / completed."""
        cart = Cart.objects.get_or_create(user=self.user)[0]
        item = CartItem.objects.create(cart=cart, product=self.prod, quantity=quantity)
        order = create_order(self.user, [item.id], self.addr.id)
        pay_order(order, "123456")
        order = Order.objects.get(pk=order.pk)
        for step in self._PUSH_TO[status]:
            step(order)
        order.refresh_from_db()
        self.assertEqual(order.status, status, "夹具没把订单推到目标状态")
        self.prod.refresh_from_db()
        return order

    def _balance(self) -> Decimal:
        return Profile.objects.get(pk=self.user.minimall_profile.pk).balance

    def _fresh(self, order) -> Order:
        """重新读一份订单 —— 模拟「另一个请求几秒前读到的那份」."""
        return Order.objects.get(pk=order.pk)

    def _fresh_refund(self, refund) -> RefundRequest:
        return RefundRequest.objects.get(pk=refund.pk)

    # ------------------------------------------------------------------
    # 申请 → 订单变 refunding
    # ------------------------------------------------------------------

    def test_request_snapshots_status_and_marks_order_refunding(self):
        """申请: 建一条 requested 的申请, 订单置退款中, 并快照申请前的状态.

        金额**不带** —— 买家申请时只表达「我要退钱」, 金额在批准时由管理员协商.
        """
        order = self._paid_order()
        refund = request_refund(order)

        self.assertEqual(refund.status, RefundRequest.Status.REQUESTED)
        self.assertIsNone(refund.amount)
        self.assertEqual(refund.order_status_before, Order.Status.PAID)
        self.assertIsNone(refund.approved_at)
        self.assertIsNone(refund.refunded_at)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REFUNDING)
        self.assertIsNone(order.refunded_at, "退款时间由打款那步写, 申请时不该有")

    def test_request_rejected_before_payment(self):
        """待付款的订单不能申请退款 —— 钱还没出去, 走取消就行."""
        cart = Cart.objects.get_or_create(user=self.user)[0]
        item = CartItem.objects.create(cart=cart, product=self.prod, quantity=1)
        pending = create_order(self.user, [item.id], self.addr.id)

        with self.assertRaises(RefundNotAllowedError):
            request_refund(pending)

        self.assertFalse(RefundRequest.objects.filter(order=pending).exists())

    def test_request_rejected_when_one_is_already_active(self):
        """同一订单只能有一条进行中的申请 —— 拿申请前的旧实例再提也一样被拒.

        判据取锁内的值: 传进来的那份实例还停在 paid, 若信它就会多建一条申请.
        """
        order = self._paid_order()
        request_refund(order)

        with self.assertRaises(RefundAlreadyInProgressError):
            request_refund(self._fresh(order))

        self.assertEqual(RefundRequest.objects.filter(order=order).count(), 1)

    def test_rejected_refund_can_be_requested_again(self):
        """被驳回后可以再提一条 —— 所以「同一时刻只有一条」做不成唯一约束."""
        order = self._paid_order()
        refund = request_refund(order)
        reject_refund(refund, note="凭证不足")

        again = request_refund(self._fresh(order))

        self.assertNotEqual(again.pk, refund.pk)
        self.assertEqual(again.status, RefundRequest.Status.REQUESTED)
        self.assertEqual(RefundRequest.objects.filter(order=order).count(), 2)

    # ------------------------------------------------------------------
    # 批准 → 订单状态不变 (仍是 refunding), 金额定死在这一步
    # ------------------------------------------------------------------

    def test_approve_records_amount_and_leaves_order_refunding(self):
        """批准只动退款单, 不动订单 —— 钱还没出账, 订单仍停在退款中."""
        order = self._paid_order()
        refund = request_refund(order)

        approved = approve_refund(
            refund, amount=Decimal("70.00"), note="协商一致退 70 元"
        )

        self.assertEqual(approved.status, RefundRequest.Status.APPROVED)
        self.assertEqual(approved.amount, Decimal("70.00"))
        self.assertEqual(approved.admin_note, "协商一致退 70 元")
        self.assertIsNotNone(approved.approved_at)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REFUNDING)
        self.assertIsNone(order.refunded_at)

    def test_approve_amount_boundaries(self):
        """金额边界: 0 与负数拒绝, 超过订单总额拒绝, 恰好等于总额通过."""
        order = self._paid_order()  # 总额 100.00

        for amount in (Decimal("0.00"), Decimal("-1.00"), Decimal("100.01")):
            with self.subTest(amount=amount):
                refund = request_refund(self._fresh(order))
                with self.assertRaises(InvalidRefundAmountError):
                    approve_refund(refund, amount=amount)
                refund.refresh_from_db()
                self.assertEqual(refund.status, RefundRequest.Status.REQUESTED)
                reject_refund(refund)  # 清场, 好让下一边界值能再提

        refund = request_refund(self._fresh(order))
        self.assertEqual(
            approve_refund(refund, amount=Decimal("100.00")).amount,
            Decimal("100.00"),
        )

    def test_approve_rejected_when_not_requested(self):
        """已批准的不能重批 —— 金额只能定一次."""
        order = self._paid_order()
        refund = request_refund(order)
        approve_refund(refund, amount=Decimal("70.00"))

        with self.assertRaises(InvalidRefundStatusError):
            approve_refund(self._fresh_refund(refund), amount=Decimal("50.00"))

        refund.refresh_from_db()
        self.assertEqual(refund.amount, Decimal("70.00"))

    def test_approve_rejected_after_reject(self):
        """已驳回的不能批准 —— 驳回是终态."""
        order = self._paid_order()
        refund = request_refund(order)
        reject_refund(refund, note="凭证不足")

        with self.assertRaises(InvalidRefundStatusError):
            approve_refund(self._fresh_refund(refund), amount=Decimal("70.00"))

    # ------------------------------------------------------------------
    # 打款 → 钱出账 + 订单置 refunded + 写 refunded_at
    # ------------------------------------------------------------------

    def test_settle_pays_negotiated_amount(self):
        """按协商金额出账 (不是订单总额), 订单置已退款并写退款时间."""
        order = self._paid_order()  # 100.00, 付款后余额 9900.00
        refund = request_refund(order)
        approve_refund(refund, amount=Decimal("70.00"))

        settled = settle_refund(refund)

        self.assertEqual(settled.status, RefundRequest.Status.REFUNDED)
        self.assertIsNotNone(settled.refunded_at)
        self.assertEqual(self._balance(), Decimal("9970.00"))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REFUNDED)
        self.assertIsNotNone(order.refunded_at)

    def test_settle_rejected_before_approval(self):
        """待处理的不能直接打款 —— 金额都还没定, 打什么."""
        order = self._paid_order()
        refund = request_refund(order)
        balance_before = self._balance()

        with self.assertRaises(InvalidRefundStatusError):
            settle_refund(refund)

        self.assertEqual(self._balance(), balance_before)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REFUNDING)

    def test_settle_twice_pays_once(self):
        """已打款的不能重打 —— 重复执行不能重复出账."""
        order = self._paid_order()
        refund = request_refund(order)
        approve_refund(refund, amount=Decimal("70.00"))
        settle_refund(refund)

        with self.assertRaises(InvalidRefundStatusError):
            settle_refund(self._fresh_refund(refund))

        self.assertEqual(self._balance(), Decimal("9970.00"))

    # ------------------------------------------------------------------
    # 驳回 → 订单**恢复**申请前的状态 (四个起始状态各来一遍)
    # ------------------------------------------------------------------

    def test_reject_restores_order_status(self):
        """四个可退款的起始状态, 驳回后都回到原样.

        驳回意味着「这笔退款不成立」, 订单该继续正常流转 —— 推到 cancelled 是错的
        (取消是买家终止订单, 驳回不是). 恢复靠快照, 不靠时间戳推断.
        """
        for status in self._PUSH_TO:
            with self.subTest(status=status):
                order = self._paid_order(status)
                refund = request_refund(order)

                rejected = reject_refund(refund, note="商品没问题")

                self.assertEqual(rejected.status, RefundRequest.Status.REJECTED)
                self.assertEqual(rejected.admin_note, "商品没问题")
                self.assertIsNotNone(rejected.rejected_at)
                order.refresh_from_db()
                self.assertEqual(order.status, status)
                self.assertIsNone(order.refunded_at)

    # ------------------------------------------------------------------
    # 与既有订单动作的关系
    # ------------------------------------------------------------------

    def test_refunding_order_cannot_be_cancelled_shipped_or_received(self):
        """退款中的订单自动不可取消 / 发货 / 收货 —— `refunding` 不在三者的白名单里.

        这是 `refunding` 挤进 `Order.Status` 白拿的一份好处 (ADR-0004): 三个既有
        函数一行都不用改. 但「不用改」得有用例钉住, 否则以后谁放宽了白名单都没人知道.
        """
        order = self._paid_order()
        request_refund(order)

        for action in (cancel_order, ship_order, receive_order):
            with (
                self.subTest(action=action.__name__),
                self.assertRaises(InvalidOrderStatusError),
            ):
                action(self._fresh(order))

        self.assertEqual(self._balance(), Decimal("9900.00"), "取消没被执行, 钱不该动")

    def test_refund_never_touches_stock(self):
        """整条退款流程前后库存一字不变.

        与 `cancel_order` 不对称, 且这条不对称是**对的**: 取消时货还没出去
        (只认 pending / paid), 退款时货已经在买家手里 —— 把库存加回来等于凭空造货.
        """
        order = self._paid_order(quantity=2)
        stock_before = Product.objects.get(pk=self.prod.pk).stock

        refund = request_refund(order)
        approve_refund(refund, amount=Decimal("70.00"))
        settle_refund(refund)

        self.assertEqual(Product.objects.get(pk=self.prod.pk).stock, stock_before)

    def test_amounts_stay_decimal_end_to_end(self):
        """全程 Decimal —— 金额一旦沾上 float, 账就对不上分了."""
        order = self._paid_order()
        refund = request_refund(order)
        approve_refund(refund, amount=Decimal("70.00"))
        settle_refund(refund)

        refund.refresh_from_db()
        self.assertIsInstance(refund.amount, Decimal)
        self.assertIsInstance(self._balance(), Decimal)
        self.assertIsInstance(order.total_amount, Decimal)
