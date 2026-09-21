"""真并发用例: 顺序调用测不出锁.

本片修的正是锁 —— 购物车的丢更新与支付的重复扣款, 都只在**两个请求同时进行**
时才发生. 顺序调用 (哪怕第二次用的是旧实例) 也照样能过, 所以这里的用例:

1. 用 `TransactionTestCase` —— `TestCase` 把每个用例包在一个事务里回滚, 线程
   看不到别人已提交的数据, 那就不叫并发了;
2. 用线程 + `Barrier` 让几个写操作尽量在同一刻发起;
3. 断言「无论怎么交错都必须成立」的结果 (总件数, 扣款次数), 而不是去复现某
   一种具体交错 —— 具体交错复现不出来, 但「不许丢, 不许扣两遍」是硬要求.
"""

import threading
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase

from app.minimall.models import (
    Cart,
    CartItem,
    Category,
    Order,
    Product,
    Profile,
    ShippingAddress,
)
from app.minimall.services import (
    InvalidOrderStatusError,
    add_to_cart,
    create_order,
    pay_order,
)

User = get_user_model()

THREADS = 4
# 线程之间会互相等锁 (MySQL 默认 innodb_lock_wait_timeout 是 50 秒), 给足余量
THREAD_TIMEOUT = 60


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
