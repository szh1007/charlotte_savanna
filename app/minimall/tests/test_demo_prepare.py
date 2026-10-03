"""演示数据准备命令 (`manage.py demo_prepare`) 的用例.

判据只有两条, 都直接对应 C04 的验收: **跑两次结果一致** (不重复下单 / 不重复充值),
**缺什么补什么** (没有未付款订单就下一笔, 余额不够就补到下限).
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils.timezone import now

from app.minimall.models import (
    Category,
    Order,
    Product,
    Profile,
    ShippingAddress,
)

User = get_user_model()

FLOOR = Decimal("1000")


class DemoPrepareTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="demo", email="demo@t.com", password="pass"
        )
        self.profile = Profile.objects.create(user=self.user, balance=Decimal("5000"))
        self.cat = Category.objects.create(name="Test", slug="demo-test")
        self.cheap = Product.objects.create(
            name="便宜货", slug="cheap", category=self.cat, price=99, stock=10
        )
        self.dear = Product.objects.create(
            name="贵货", slug="dear", category=self.cat, price=8000, stock=10
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

    def _run(self, **kwargs):
        out = StringIO()
        call_command("demo_prepare", user_id=self.user.id, stdout=out, **kwargs)
        return out.getvalue()

    def _pending_order(self, order_no: str) -> Order:
        """直接插一笔待付款订单 (不走服务层: 这里要的是「库里已经有」这个前提)."""
        return Order.objects.create(
            order_no=order_no,
            user=self.user,
            status=Order.Status.PENDING,
            total_amount=Decimal("5"),
            shipping_address_snapshot={},
        )

    def test_creates_pending_order_when_missing(self):
        output = self._run()

        order = Order.objects.get(user=self.user, status=Order.Status.PENDING)
        self.assertEqual(order.total_amount, Decimal("99"))
        self.assertIn(order.order_no, output)
        # 建的是**最便宜**的那件: 演示花的钱要可预期, 不能用余额去赌
        self.assertEqual([i.product_name for i in order.items.all()], ["便宜货"])

    def test_second_run_does_not_duplicate_order(self):
        first = self._run()
        second = self._run()

        self.assertEqual(
            Order.objects.filter(user=self.user, status=Order.Status.PENDING).count(), 1
        )
        # 复用时报的是**同一笔** (演示要付的那一笔不会随着重跑漂走)
        order_no = Order.objects.get(user=self.user).order_no
        self.assertIn(order_no, first)
        self.assertIn(order_no, second)

    def test_reuses_existing_pending_order(self):
        existing = self._pending_order("202601010000000000000001")

        output = self._run()

        self.assertIn(existing.order_no, output)
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)
        self.cheap.refresh_from_db()
        self.assertEqual(self.cheap.stock, 10)  # 没有为它再占一次库存

    def test_reports_the_newest_pending_order(self):
        """报「最新」而不是「最早」: 剧本那句「最近的那笔」, 模型挑的就是最新那笔."""

        # 先建的那笔才是「最近」的 —— 这样两种排序给出的答案是两笔不同的订单,
        # 断言才有区分度 (created_at 是 auto_now_add, 只能建完再往回拨)
        newest = self._pending_order("202601010000000000000002")
        earlier = self._pending_order("202601010000000000000001")
        Order.objects.filter(pk=earlier.pk).update(
            created_at=now() - timedelta(minutes=10)
        )

        output = self._run()

        self.assertIn(newest.order_no, output)
        self.assertNotIn(earlier.order_no, output)

    def test_creates_profile_for_buyer_without_one(self):
        self.profile.delete()

        output = self._run()

        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.balance, FLOOR)
        self.assertIn("没有 Profile", output)

    def test_balance_floor_is_configurable(self):
        self.profile.balance = Decimal("500")
        self.profile.save(update_fields=["balance"])

        self._run(balance_floor=Decimal("2000"))

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance, Decimal("2000"))

    def test_tops_up_balance_to_floor(self):
        self.profile.balance = Decimal("12.34")
        self.profile.save(update_fields=["balance"])

        output = self._run()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance, FLOOR)
        self.assertIn("12.34", output)
        self.assertIn("1000.00", output)

    def test_keeps_balance_when_already_above_floor(self):
        self._run()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance, Decimal("5000"))

    def test_creates_placeholder_address_when_buyer_has_none(self):
        self.addr.delete()

        self._run()

        address = ShippingAddress.objects.get(user=self.user)
        self.assertIn("演示", address.receiver_name)

    def test_reports_the_same_state_twice(self):
        """幂等的口径: 两次跑完, 买家看得见的那几个数一模一样."""

        def snapshot():
            self.profile.refresh_from_db()
            order = Order.objects.get(user=self.user, status=Order.Status.PENDING)
            return (self.profile.balance, order.order_no, self.cheap.stock)

        self._run()
        before = snapshot()
        self._run()

        self.assertEqual(snapshot(), before)

    def test_unknown_buyer_fails_loudly(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("demo_prepare", user_id=999999, stdout=StringIO())

        self.assertIn("999999", str(ctx.exception))

    def test_no_purchasable_product_fails_loudly(self):
        Product.objects.update(is_active=False)

        with self.assertRaises(CommandError) as ctx:
            self._run()

        self.assertIn("商品", str(ctx.exception))
