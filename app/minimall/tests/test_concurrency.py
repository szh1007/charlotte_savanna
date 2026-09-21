"""真并发用例: 顺序调用测不出锁.

本片修的正是锁 —— 购物车的丢更新、支付的重复扣款、退款的双提与双打款, 都只在
**两个请求同时进行**时才发生. 顺序调用 (哪怕第二次用的是旧实例) 也照样能过, 所以
这里的用例:

1. 用 `TransactionTestCase` —— `TestCase` 把每个用例包在一个事务里回滚, 线程
   看不到别人已提交的数据, 那就不叫并发了;
2. 用线程 + `Barrier` 让几个写操作尽量在同一刻发起;
3. 断言「无论怎么交错都必须成立」的结果 (总件数, 扣款次数), 而不是去复现某
   一种具体交错 —— 具体交错复现不出来, 但「不许丢, 不许扣两遍」是硬要求.
4. 光靠第 2 条会合不够: 判据读完之后到写入之间的窗口只有零点几毫秒, 线程往往
   一前一后就过去了 (这类用例在**拆掉锁的实现**上也可能侥幸通过). 退款的四条
   写入窗口尤其窄, 所以那两条另加一个**窗口内的会合点**: 两个线程都走到窗口正中
   才放行. 没有锁时它们一起冲过判据 (于是复现出双提 / 双打款), 有锁时后到的那个
   卡在锁上根本到不了 —— 等超时放行, 那时判据已经是锁内 re-fetch 出来的新状态了.
"""

import contextlib
import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone

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
    InvalidOrderStatusError,
    InvalidRefundStatusError,
    RefundAlreadyInProgressError,
    add_to_cart,
    approve_refund,
    create_order,
    pay_order,
    request_refund,
    settle_refund,
)

User = get_user_model()

THREADS = 4
# 线程之间会互相等锁 (MySQL 默认 innodb_lock_wait_timeout 是 50 秒), 给足余量
THREAD_TIMEOUT = 60
# 窗口内会合的等待上限: 没锁时两个线程都在窗口里, 会合是毫秒级的事;
# 有锁时另一个线程到不了, 就等这么久再放行 (它卡在锁上等的是我们提交)
RENDEZVOUS_TIMEOUT = 2.0


class ConcurrentTestBase(TransactionTestCase):
    """并发用例的脚手架: 起线程, 等线程, 把线程里的异常捞回来."""

    def run_concurrently(self, worker, count: int) -> None:
        """起 count 个线程同时在跑 worker, 返回时保证它们都已经结束.

        线程里的异常不会自己冒到主线程 —— 不捞回来, 用例就会「静默通过」.
        """
        errors: list[BaseException] = []
        barrier = threading.Barrier(count)

        def wrapped():
            try:
                connections.close_all()  # 线程有自己的连接, 先关掉继承来的
                barrier.wait(timeout=THREAD_TIMEOUT)
                worker()
            except BaseException as exc:  # 见上: 不捞回来就等于没测
                errors.append(exc)
            finally:
                connections.close_all()  # 别把连接 (和它持有的锁) 留给测试库

        threads = [threading.Thread(target=wrapped) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT)

        still_alive = [t.name for t in threads if t.is_alive()]
        self.assertEqual(still_alive, [], "线程没跑完 (超时), 多半是卡在锁上")
        self.assertEqual(errors, [])


class ConcurrentAddToCartTest(ConcurrentTestBase):
    """并发加购: 同一买家, 同一商品."""

    def test_concurrent_add_does_not_lose_updates(self):
        """4 个请求同时各加 1 件 → 车里就是 4 件.

        老实现没有锁, 这几个请求会互相踩, 两种踩法都得防: 条目还不存在时, 它们
        会一起认为「该新建」而被 unique_together 拦下 (IntegrityError); 条目已存
        在时则是「读出来 + 加 + 写回去」, 后写的把先写的盖掉 (丢更新).
        """
        user = User.objects.create_user(
            username="race-cart", email="rc@t.com", password="pass"
        )
        Cart.objects.create(user=user)  # 车先建好: 这里测的是加购, 不是建车
        cat = Category.objects.create(name="Race", slug="race-cart")
        prod = Product.objects.create(
            name="P", slug="p-race", category=cat, price=10.00, stock=100
        )

        def worker():
            add_to_cart(user, product_id=prod.id, quantity=1)

        self.run_concurrently(worker, THREADS)

        item = CartItem.objects.get(cart__user=user, product=prod)
        self.assertEqual(item.quantity, THREADS)

    def test_concurrent_add_cannot_exceed_stock(self):
        """4 个请求同时各加 1 件, 库存只有 2 → 车里最多 2 件 (不许超卖)."""
        user = User.objects.create_user(
            username="race-stock", email="rs@t.com", password="pass"
        )
        Cart.objects.create(user=user)
        cat = Category.objects.create(name="Race2", slug="race-stock")
        prod = Product.objects.create(
            name="P2", slug="p-race2", category=cat, price=10.00, stock=2
        )

        def worker():
            add_to_cart(user, product_id=prod.id, quantity=1)

        self.run_concurrently(worker, THREADS)

        item = CartItem.objects.get(cart__user=user, product=prod)
        self.assertEqual(item.quantity, 2)


class ConcurrentPayOrderTest(ConcurrentTestBase):
    """并发支付: 买家双击支付按钮."""

    def test_concurrent_pay_deducts_once(self):
        """两个请求同时付同一张单 → 成功一次, 被拒一次, 余额只扣一次.

        两个请求各自读到「待付款」的订单 (同一个实例), 谁都觉得自己该付 ——
        老实现于是扣了两遍钱. 现在第二个请求会在锁内 re-fetch 出真实状态.
        """
        user = User.objects.create_user(
            username="race-pay", email="rp@t.com", password="pass"
        )
        Profile.objects.create(user=user, balance=10000)
        profile = Profile.objects.get(user=user)
        profile.set_payment_password("123456")
        profile.save()
        cat = Category.objects.create(name="Race3", slug="race-pay")
        prod = Product.objects.create(
            name="P3", slug="p-race3", category=cat, price=10.00, stock=10
        )
        address = ShippingAddress.objects.create(
            user=user,
            receiver_name="X",
            phone="1",
            province="A",
            city="B",
            district="C",
            detail="D",
        )
        cart = Cart.objects.create(user=user)
        item = CartItem.objects.create(cart=cart, product=prod, quantity=1)
        order = create_order(user, [item.id], address.id)
        stale = Order.objects.get(pk=order.pk)  # 两个请求手里都是这一份
        outcomes: list[str] = []

        def worker():
            try:
                pay_order(stale, "123456")
            except InvalidOrderStatusError:
                outcomes.append("rejected")
            else:
                outcomes.append("paid")

        self.run_concurrently(worker, 2)

        self.assertEqual(sorted(outcomes), ["paid", "rejected"])
        self.assertEqual(Profile.objects.get(user=user).balance, Decimal("9990.00"))
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.PAID)


class ConcurrentRefundTest(ConcurrentTestBase):
    """并发退款: 买家两个设备同时申请, 管理员两个标签页同时打款."""

    def _paid_order(self, username: str, slug: str) -> Order:
        """建一个买家与一张已付款订单, 供下面两个用例复用."""
        user = User.objects.create_user(
            username=username, email=f"{username}@t.com", password="pass"
        )
        Profile.objects.create(user=user, balance=10000)
        profile = Profile.objects.get(user=user)
        profile.set_payment_password("123456")
        profile.save()
        cat = Category.objects.create(name=username, slug=slug)
        prod = Product.objects.create(
            name="P", slug=f"p-{slug}", category=cat, price=10.00, stock=10
        )
        address = ShippingAddress.objects.create(
            user=user,
            receiver_name="X",
            phone="1",
            province="A",
            city="B",
            district="C",
            detail="D",
        )
        cart = Cart.objects.create(user=user)
        item = CartItem.objects.create(cart=cart, product=prod, quantity=1)
        order = create_order(user, [item.id], address.id)
        pay_order(order, "123456")
        return Order.objects.get(pk=order.pk)

    def test_concurrent_request_creates_only_one(self):
        """两个请求同时申请退款 → 只建起一条, 另一个被拒.

        「同一订单只允许一条进行中的申请」是应用层校验, 不能靠唯一约束表达 (驳回
        后允许再提), 所以必须有锁兜着: 没有锁时两个请求都会看到「订单是 paid, 没有
        申请」, 于是建出两条 —— 那张订单就有了两笔各自能被打款的退款.

        会合点卡在**判据读完、插入之前** (patch 掉建申请那一步): 顺序调用测不出这个
        bug, 得让两个线程在窗口里碰头才复现得出来.
        """
        order = self._paid_order("race-refund", "race-refund")
        outcomes: list[str] = []
        gate = threading.Barrier(2)
        real_create = RefundRequest.objects.create

        def create_after_rendezvous(*args, **kwargs):
            with contextlib.suppress(threading.BrokenBarrierError):
                gate.wait(timeout=RENDEZVOUS_TIMEOUT)
            return real_create(*args, **kwargs)

        def worker():
            try:
                request_refund(Order.objects.get(pk=order.pk))
            except RefundAlreadyInProgressError:
                outcomes.append("rejected")
            else:
                outcomes.append("requested")

        with mock.patch.object(
            RefundRequest.objects, "create", side_effect=create_after_rendezvous
        ):
            self.run_concurrently(worker, 2)

        self.assertEqual(sorted(outcomes), ["rejected", "requested"])
        self.assertEqual(RefundRequest.objects.filter(order_id=order.pk).count(), 1)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.REFUNDING)

    def test_concurrent_settle_pays_once(self):
        """两个请求同时打款同一笔已批准的退款 → 只出账一次.

        与 09 的并发支付同一类: 两个标签页各自读到「已批准」, 都觉得自己该打钱.
        判据取锁内 re-fetch 的值, 第二个请求看到的是 `refunded`.

        会合点卡在**判据判完、钱还没动**那一刻 —— `settle_refund` 里那一次
        `timezone.now()` 正好在那里.
        """
        order = self._paid_order("race-settle", "race-settle")
        refund = request_refund(order)
        approve_refund(refund, amount=Decimal("7.00"))  # 订单总额 10.00, 协商退 7
        stale = RefundRequest.objects.get(pk=refund.pk)  # 两个标签页手里都是这份
        outcomes: list[str] = []
        gate = threading.Barrier(2)
        real_now = timezone.now

        def now_after_rendezvous():
            with contextlib.suppress(threading.BrokenBarrierError):
                gate.wait(timeout=RENDEZVOUS_TIMEOUT)
            return real_now()

        def worker():
            try:
                settle_refund(stale)
            except InvalidRefundStatusError:
                outcomes.append("rejected")
            else:
                outcomes.append("settled")

        with mock.patch(
            "app.minimall.services.timezone.now", side_effect=now_after_rendezvous
        ):
            self.run_concurrently(worker, 2)

        self.assertEqual(sorted(outcomes), ["rejected", "settled"])
        self.assertEqual(
            Profile.objects.get(user=order.user).balance, Decimal("9997.00")
        )
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.REFUNDED)
