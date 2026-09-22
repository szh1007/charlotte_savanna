"""退款审批入口 (Admin) 的测试.

接缝: Django 测试客户端直接打 changelist —— 三个动作与中间页表单的两段式都在这层
验. 业务规则归 `test_services.py`, 这里管的是「管理员那条路走不走得通」, 尤其是
**金额校验发生在提交之前**: 填错当场看见, 不用提交两次才知道.
"""

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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
from app.minimall.services import create_order, pay_order, request_refund

User = get_user_model()

CHANGELIST = "admin:minimall_refundrequest_changelist"


class RefundAdminTest(TestCase):
    """订单总额 100.00 的一笔待处理退款, 前面是审批要动的那几步."""

    def setUp(self):
        self.staff = User.objects.create_superuser(
            username="admin", email="admin@t.com", password="pass"
        )
        self.client.force_login(self.staff)

        self.buyer = User.objects.create_user(
            username="buyer", email="buyer@t.com", password="pass"
        )
        Profile.objects.create(user=self.buyer, balance=10000)
        profile = Profile.objects.get(user=self.buyer)
        profile.set_payment_password("123456")
        profile.save()
        self.cat = Category.objects.create(name="Admin", slug="admin-refund")
        self.prod = Product.objects.create(
            name="P", slug="p-admin-refund", category=self.cat, price=100.00, stock=10
        )
        ShippingAddress.objects.create(
            user=self.buyer,
            receiver_name="X",
            phone="1",
            province="A",
            city="B",
            district="C",
            detail="D",
        )
        self.order = self._new_paid_order()
        self.refund = request_refund(self.order)

    def _new_paid_order(self) -> Order:
        """再买一单并付掉 (驳回后可以再提, 所以「第二笔申请」要换一张订单)."""
        cart = Cart.objects.get_or_create(user=self.buyer)[0]
        item = CartItem.objects.create(cart=cart, product=self.prod, quantity=1)
        order = create_order(self.buyer, [item.id], self.buyer.addresses.first().id)
        pay_order(order, "123456")
        return Order.objects.get(pk=order.pk)

    def _post_action(self, name, refunds, *, action=None, **extra):
        """发一个动作请求 —— 默认只带**一个** `action` 键.

        这个形状在真实浏览器里**不存在**: admin 的 `.actions` 里那个 select 与模板
        注入的按钮同名, 请求体里因此有两个 `action` (要保真的形状用
        `_post_like_a_browser`). 单键的请求体测不到「动作名取第几个」这件事 ——
        缺陷 B (中间页 hidden 的动作名拿到空串, 第二次提交变成「未选择动作」) 就是在
        这样的用例下全绿活到真机验收的.

        Args:
            action: 覆盖请求体里 `action` 键的值 (默认就是动作名).
        """
        follow = extra.pop("follow", False)
        return self.client.post(
            reverse(CHANGELIST),
            {
                "action": name if action is None else action,
                "_selected_action": [r.pk for r in refunds],
                **extra,
            },
            follow=follow,
        )

    def _post_like_a_browser(self, name, refunds, **extra):
        """照真实浏览器的请求体发: **两个**同名 `action`, 按钮在前, 隐藏的 select 在后.

        取第一个是 Django 自己的语义 (`response_action` 用
        `getlist("action")[action_index]`, index 默认 0); `QueryDict.get` 取的是
        **最后一个**, 那正是空 select 的值.
        """
        return self._post_action(name, refunds, action=[name, ""], **extra)

    def _submit_middle_page(self, action, refund, **fields):
        """按中间页的形状提交第二步: 单个 `action` (值取自那页的 hidden) + 选中项.

        与真实浏览器发出来的那个请求体同形 —— 所以第一步要是把动作名写空了, 这一步
        就没有动作可执行 (Django 重渲染列表, 不返回 302).
        """
        return self.client.post(
            reverse(CHANGELIST),
            {
                "action": action,
                "_selected_action": [refund.pk],
                "confirm": "yes",
                **fields,
            },
        )

    def _amount_field(self) -> str:
        return f"amount_{self.refund.pk}"

    # ------------------------------------------------------------------
    # 批准: 两段式 —— 先渲染表单, 填好提交才执行
    # ------------------------------------------------------------------

    def test_approve_action_offers_prefilled_form(self):
        """点「批准」先出表单, 金额预填订单总额 —— 全退是最常见的那一种."""
        response = self._post_action("action_approve_refunds", [self.refund])

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.fields[self._amount_field()].initial, Decimal("100.00"))
        self.assertContains(
            response, self._amount_field(), msg_prefix="金额框没渲染出来"
        )
        self.refund.refresh_from_db()
        self.assertEqual(
            self.refund.status, RefundRequest.Status.REQUESTED, "还没确认, 不该动数据"
        )

    def test_approve_form_rejects_amount_over_order_total(self):
        """金额超过订单总额: 当场报错, 不执行 —— 这是放在表单里的那道校验.

        服务函数里也拦, 但管理员不该提交两次才知道填错了.
        """
        response = self._post_action(
            "action_approve_refunds",
            [self.refund],
            confirm="yes",
            **{self._amount_field(): "100.01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self._amount_field(), response.context["form"].errors)
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRequest.Status.REQUESTED)
        self.assertIsNone(self.refund.amount)

    def test_approve_form_rejects_zero_amount(self):
        """0 元不算退款 —— 表单挡在提交之前."""
        response = self._post_action(
            "action_approve_refunds",
            [self.refund],
            confirm="yes",
            **{self._amount_field(): "0"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self._amount_field(), response.context["form"].errors)
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRequest.Status.REQUESTED)

    def test_approve_action_records_negotiated_amount_and_note(self):
        """批准 70 (订单总额 100) → 退款单记下金额与备注, 订单仍停在退款中."""
        response = self._post_action(
            "action_approve_refunds",
            [self.refund],
            confirm="yes",
            **{self._amount_field(): "70.00", "note": "协商一致退 70 元"},
        )

        self.assertEqual(response.status_code, 302, "执行完回列表页, 免得刷新重放")
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRequest.Status.APPROVED)
        self.assertEqual(self.refund.amount, Decimal("70.00"))
        self.assertEqual(self.refund.admin_note, "协商一致退 70 元")
        self.order.refresh_from_db()  # 申请退款改的是锁内那份, 手里这份是旧的
        self.assertEqual(self.order.status, Order.Status.REFUNDING)

    def test_failed_row_does_not_stop_the_batch(self):
        """批量里一条状态不对, 其余照常执行 —— 管理员要看到「哪一条没成、为什么」."""
        other = request_refund(self._new_paid_order())
        self.refund.status = RefundRequest.Status.REJECTED
        self.refund.save(update_fields=["status"])

        response = self._post_action(
            "action_approve_refunds",
            [self.refund, other],
            confirm="yes",
            follow=True,
            **{
                self._amount_field(): "70.00",
                f"amount_{other.pk}": "50.00",
                "note": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.refund.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRequest.Status.REJECTED)
        self.assertEqual(other.status, RefundRequest.Status.APPROVED)
        self.assertEqual(other.amount, Decimal("50.00"))
        reported = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any(self.refund.order.order_no in m for m in reported),
            f"没说明哪一条没成: {reported}",
        )

    # ------------------------------------------------------------------
    # 打款 / 驳回
    # ------------------------------------------------------------------

    def test_settle_action_pays_approved_amount(self):
        """打款把协商金额打到余额上, 订单置已退款."""
        self.refund.status = RefundRequest.Status.APPROVED
        self.refund.amount = Decimal("70.00")
        self.refund.save(update_fields=["status", "amount"])

        response = self._post_action("action_settle_refunds", [self.refund])

        self.assertEqual(response.status_code, 302, "执行完回列表页, 免得刷新重放")
        self.refund.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRequest.Status.REFUNDED)
        self.assertEqual(self.order.status, Order.Status.REFUNDED)
        self.assertEqual(
            Profile.objects.get(user=self.buyer).balance, Decimal("9970.00")
        )

    def test_reject_action_restores_order_status_with_reason(self):
        """驳回: 订单回到申请前的状态, 原因记在备注里 (故事 18 要读它)."""
        response = self._post_action(
            "action_reject_refunds", [self.refund], confirm="yes", note="商品没问题"
        )

        self.assertEqual(response.status_code, 302, "执行完回列表页, 免得刷新重放")
        self.refund.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRequest.Status.REJECTED)
        self.assertEqual(self.refund.admin_note, "商品没问题")
        self.assertEqual(self.order.status, Order.Status.PAID)

    # ------------------------------------------------------------------
    # 两段式在**真实请求体**下走得通 (缺陷 B: 2026-09-22 真机验收点出,
    # 「测试全绿而人点不动」—— 就是上面 `_post_action` 那个单键的形状漏掉的)
    # ------------------------------------------------------------------

    def test_the_middle_page_carries_the_action_of_the_clicked_button(self):
        """两个同名 `action` 时, 中间页 hidden 里写的是按钮那个, 不是空串.

        写空串的后果在下一步: 浏览器提交中间页时带回去的 `action` 是空的, Django
        找不到动作就重渲染列表, 管理员的「批准 / 驳回」永远走不到执行那一步.
        """
        response = self._post_like_a_browser("action_approve_refunds", [self.refund])

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'name="action" value="action_approve_refunds"',
            msg_prefix="中间页没拿到动作名 (提交回来会变成「未选择动作」)",
        )

    def test_approve_takes_both_steps_a_browser_takes(self):
        """批准: 第一步渲染表单 → 第二步拿**中间页自己的**动作名回发 → 金额真定下来.

        第二步不手写动作名, 而是从中间页的 hidden 里读 —— 手写的话这个用例在缺陷 B
        下也会是绿的 (它替浏览器把那个空串补对了), 那正是假绿灯的形状.
        """
        middle = self._post_like_a_browser("action_approve_refunds", [self.refund])

        response = self._submit_middle_page(
            self._hidden_action(middle),
            self.refund,
            **{self._amount_field(): "70.00", "note": "协商一致退 70 元"},
        )

        self.assertEqual(response.status_code, 302, "执行完回列表页, 免得刷新重放")
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRequest.Status.APPROVED)
        self.assertEqual(self.refund.amount, Decimal("70.00"))

    @staticmethod
    def _hidden_action(response) -> str:
        """中间页里那个 hidden `action` 的值 —— 浏览器第二步就是拿它回发的."""
        match = re.search(r'name="action" value="([^"]*)"', response.content.decode())
        assert match is not None, "中间页没渲染出 hidden action"
        return match.group(1)

    def test_reject_takes_both_steps_a_browser_takes(self):
        """驳回: 同样的两段式 (它与批准共用 `_render_action_form`)."""
        middle = self._post_like_a_browser("action_reject_refunds", [self.refund])

        response = self._submit_middle_page(
            self._hidden_action(middle), self.refund, note="商品没问题"
        )

        self.assertEqual(response.status_code, 302)
        self.refund.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRequest.Status.REJECTED)
        self.assertEqual(self.refund.admin_note, "商品没问题")
        self.assertEqual(self.order.status, Order.Status.PAID, "订单恢复申请前的状态")

    # ------------------------------------------------------------------
    # 入口本身的约束
    # ------------------------------------------------------------------

    def test_delete_is_refused(self):
        """退款单不许删 —— 删掉一条进行中的申请会把订单永久卡在 refunding.

        (取消 / 发货 / 收货都不认 `refunding`, 删了申请又不能再提, 那张订单就出不来了.)
        """
        response = self.client.get(
            reverse("admin:minimall_refundrequest_delete", args=[self.refund.pk])
        )
        self.assertEqual(response.status_code, 403)
