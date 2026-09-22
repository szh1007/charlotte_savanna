import contextlib
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.template.response import TemplateResponse
from django.utils import timezone
from mptt.admin import MPTTModelAdmin

from .models import (
    Category,
    Order,
    OrderItem,
    Product,
    ProductImage,
    Profile,
    RefundRequest,
)
from .services import (
    OrderServiceError,
    approve_refund,
    reject_refund,
    settle_refund,
)

# 替换 Django 默认的"删除所选"为"批量删除"
admin.site.disable_action("delete_selected")


@admin.action(description="批量删除")
def batch_delete(modeladmin, request, queryset):
    queryset.delete()


class BatchDeleteMixin:
    """Mixin — 添加批量删除 action 到每个 Admin."""

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions["batch_delete"] = (batch_delete, "batch_delete", "批量删除")
        return actions


# ---------------------------------------------------------------------------
# 自定义 Filter
# ---------------------------------------------------------------------------


class NumericRangeFilter(admin.SimpleListFilter):
    """通用数值范围筛选 — 支持自定义输入最小值/最大值."""

    template = "admin/filter_numeric_range.html"

    def __init__(self, request, params, model, model_admin):
        super().__init__(request, params, model, model_admin)
        self._field_name = getattr(self, "field_name", "")

    def lookups(self, request, model_admin):
        return [("range", "自定义范围")]

    def queryset(self, request, queryset):
        if not self._field_name:
            return queryset
        # 读取 GET 参数中的 min/max 值
        min_key = f"min_{self.parameter_name}"
        max_key = f"max_{self.parameter_name}"
        min_val = request.GET.get(min_key, "")
        max_val = request.GET.get(max_key, "")
        if not min_val and not max_val:
            return queryset
        min_val = request.GET.get(f"min_{self._field_name}", "")
        max_val = request.GET.get(f"max_{self._field_name}", "")
        if min_val:
            with contextlib.suppress(ValueError, TypeError):
                queryset = queryset.filter(
                    **{f"{self._field_name}__gte": float(min_val)}
                )
        if max_val:
            with contextlib.suppress(ValueError, TypeError):
                queryset = queryset.filter(
                    **{f"{self._field_name}__lte": float(max_val)}
                )
        return queryset

    def has_output(self):
        return True

    def choices(self, changelist):
        yield {
            "selected": self.value() == "range",
            "query_string": changelist.get_query_string({self.parameter_name: "range"}),
            "display": "自定义范围",
            "min_name": f"min_{self.parameter_name}",
            "max_name": f"max_{self.parameter_name}",
        }


def make_range_filter(field_name: str, title: str):
    """工厂函数 — 创建指定字段的范围筛选器."""
    return type(
        f"{field_name.title()}RangeFilter",
        (NumericRangeFilter,),
        {"title": title, "parameter_name": field_name, "field_name": field_name},
    )


class HasPhoneFilter(admin.SimpleListFilter):
    title = "是否设置手机号"
    parameter_name = "has_phone"

    def lookups(self, request, model_admin):
        return [("yes", "已设置"), ("no", "未设置")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.exclude(phone__isnull=True).exclude(phone="")
        if self.value() == "no":
            return queryset.filter(phone__isnull=True) | queryset.filter(phone="")


# ---------------------------------------------------------------------------
# Admin 注册
# ---------------------------------------------------------------------------


@admin.register(Profile)
class ProfileAdmin(BatchDeleteMixin, admin.ModelAdmin):
    list_display = ["user", "phone", "balance", "avatar_version"]
    list_filter = [
        HasPhoneFilter,
        make_range_filter("balance", "余额范围"),
        "avatar_version",
    ]
    search_fields = ["user__username", "phone"]
    readonly_fields = ["avatar_version", "avatar_updated_at"]
    exclude = ["payment_password"]
    ordering = ["-user__date_joined"]


@admin.register(Category)
class CategoryAdmin(BatchDeleteMixin, MPTTModelAdmin):
    list_display = ["name", "slug", "sort_order", "is_active"]
    list_editable = ["sort_order"]
    list_filter = ["is_active", "parent"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 3


@admin.register(Product)
class ProductAdmin(BatchDeleteMixin, admin.ModelAdmin):
    list_display = [
        "name",
        "category",
        "price",
        "stock",
        "sort_order",
        "is_active",
        "is_featured",
        "created_at",
    ]
    list_editable = ["sort_order"]
    list_filter = [
        "category",
        "is_active",
        "is_featured",
        make_range_filter("price", "价格范围"),
        make_range_filter("stock", "库存范围"),
    ]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductImageInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["product_name", "product_price", "quantity", "subtotal"]
    can_delete = False


@admin.register(Order)
class OrderAdmin(BatchDeleteMixin, admin.ModelAdmin):
    list_display = ["order_no", "user", "status", "total_amount", "created_at"]
    list_filter = [
        "status",
        "created_at",
        make_range_filter("total_amount", "金额范围"),
    ]
    search_fields = ["order_no", "user__username"]
    readonly_fields = [
        "order_no",
        "total_amount",
        "shipping_address_snapshot",
        "paid_at",
        "shipped_at",
        "received_at",
        "cancelled_at",
        "refunded_at",
        "created_at",
        "updated_at",
    ]
    inlines = [OrderItemInline]
    actions = ["action_ship_orders"]
    ordering = ["-created_at"]

    @admin.action(description="批量发货")
    def action_ship_orders(self, request, queryset):
        updated = queryset.filter(status=Order.Status.PAID).update(
            status=Order.Status.SHIPPED, shipped_at=timezone.now()
        )
        self.message_user(request, f"已发货 {updated} 个订单.")


# ---------------------------------------------------------------------------
# 退款审批
# ---------------------------------------------------------------------------


class RefundApproveForm(forms.Form):
    """批准退款的中间页表单: 每个被选中的退款单一个金额输入框.

    Args:
        refunds: 被选中的退款单 —— 金额上限来自各自订单的总额, 所以字段得按选中项
            动态生成 (一笔退一个价, 不能共用一个输入框).

    校验放在表单里, 不只在服务函数里: 管理员填错了当场看见, 不用提交两次才知道.
    服务函数里那道仍然留着 —— 它是唯一入口的兜底, admin 之外还有别的调用方.
    """

    note = forms.CharField(
        label="管理员备注",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="写清协商结果, 例如「协商一致退 70 元」; 买家申请时会看到这句",
    )

    def __init__(self, *args, refunds=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._refunds = {refund.pk: refund for refund in refunds}
        for refund in self._refunds.values():
            self.fields[self.amount_field(refund)] = forms.DecimalField(
                label=(
                    f"{refund.order.order_no} 退款金额"
                    f" (上限 {refund.order.total_amount})"
                ),
                max_digits=10,
                decimal_places=2,
                min_value=Decimal("0.01"),
                initial=refund.order.total_amount,
            )
        # 金额在前、备注在后 —— 类字段先入 self.fields, 这里把它挪到末尾
        self.fields["note"] = self.fields.pop("note")

    @staticmethod
    def amount_field(refund) -> str:
        """金额输入框的字段名 —— 模板与动作都按同一个规则拼这个名字."""
        return f"amount_{refund.pk}"

    def clean(self):
        cleaned = super().clean()
        for refund in self._refunds.values():
            name = self.amount_field(refund)
            amount = cleaned.get(name)
            if amount is not None and amount > refund.order.total_amount:
                self.add_error(name, f"不能超过订单总额 {refund.order.total_amount}")
        return cleaned


class RefundRejectForm(forms.Form):
    """驳回退款的中间页表单: 只填原因 (退款单本身没有要改的字段)."""

    note = forms.CharField(
        label="驳回原因",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="买家问「为什么被驳回」时, 助手答的就是这句",
    )


@admin.register(RefundRequest)
class RefundRequestAdmin(admin.ModelAdmin):
    """退款申请的审批入口 —— 订单与退款单的**状态**只能由这三个动作推动.

    状态字段全是只读的, 所以变更页改不动状态机 (`admin_note` 例外: 那是个备注, 不
    参与状态机, 允许管理员事后补一句).

    没挂 BatchDeleteMixin (其它 admin 都挂了): 这张表是退款流程的流水本身
    (ADR-0004), 撤掉一笔申请该走「驳回」. 也不许手工新增和删除 —— 申请只能由买家
    发起 (`order_status_before` 是申请那一刻取的快照), 而**删掉一条进行中的申请会
    把订单永久卡在 `refunding`**: 取消 / 发货 / 收货都不认这个状态, 又不能再提申请.
    """

    list_display = ["order_no", "buyer", "amount", "status", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["order__order_no", "order__user__username"]
    readonly_fields = [
        "order",
        "status",
        "amount",
        "order_status_before",
        "created_at",
        "approved_at",
        "refunded_at",
        "rejected_at",
    ]
    actions = [
        "action_approve_refunds",
        "action_settle_refunds",
        "action_reject_refunds",
    ]
    ordering = ["-created_at"]

    def get_queryset(self, request):
        # 列表与中间页都要看订单号与买家 —— 不 join 就是每行两次查询
        return super().get_queryset(request).select_related("order", "order__user")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="订单号", ordering="order__order_no")
    def order_no(self, obj):
        return obj.order.order_no

    @admin.display(description="买家", ordering="order__user__username")
    def buyer(self, obj):
        return obj.order.user.username

    # ------------------------------------------------------------------
    # 三个动作: 批准 / 打款 / 驳回
    # ------------------------------------------------------------------
    # 「批准」与「驳回」都要管理员先填点什么 (金额 / 原因), 所以走中间页表单: 第一次
    # 点动作渲染表单, 填好再提交才真执行. 「打款」没有要填的, 直接执行 —— 从列表里
    # 选中再选动作点「执行」本身已经是两步.

    @admin.action(description="批准退款 (填协商金额)")
    def action_approve_refunds(self, request, queryset):
        if request.POST.get("confirm"):
            form = RefundApproveForm(request.POST, refunds=queryset)
            if form.is_valid():
                self._run_each(
                    request,
                    queryset,
                    "批准",
                    lambda refund: approve_refund(
                        refund,
                        amount=form.cleaned_data[
                            RefundApproveForm.amount_field(refund)
                        ],
                        note=form.cleaned_data["note"],
                    ),
                )
                return None
        else:
            form = RefundApproveForm(refunds=queryset)

        return self._render_action_form(request, queryset, form, "批准退款")

    @admin.action(description="打款 (按批准的金额出账)")
    def action_settle_refunds(self, request, queryset):
        self._run_each(request, queryset, "打款", settle_refund)

    @admin.action(description="驳回退款 (填原因)")
    def action_reject_refunds(self, request, queryset):
        if request.POST.get("confirm"):
            form = RefundRejectForm(request.POST)
            if form.is_valid():
                self._run_each(
                    request,
                    queryset,
                    "驳回",
                    lambda refund: reject_refund(
                        refund, note=form.cleaned_data["note"]
                    ),
                )
                return None
        else:
            form = RefundRejectForm()

        return self._render_action_form(request, queryset, form, "驳回退款")

    def _render_action_form(self, request, queryset, form, title: str):
        """渲染中间页 —— 表单填错时回到这里重填, 填对了才执行.

        动作名**取第一个** `action` (`getlist[0]`), 与 Django 自己的
        `response_action` 同一条语义: 请求体里有两个同名 `action` (项目注入的按钮
        一个, 被藏起来的 select 一个), `QueryDict.get` 拿的是**最后一个** —— 那正是
        空 select 的值, 中间页的 hidden 会带着它回来, 第二次提交就没了动作可执行.
        (按钮排在前是模板保证的: 它插在 `#changelist-form` 的第一个子节点上.)
        """
        return TemplateResponse(
            request,
            "admin/minimall/refund_action.html",
            {
                **self.admin_site.each_context(request),
                "title": title,
                "refunds": queryset,
                "form": form,
                "opts": self.model._meta,
                "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
                "action_name": request.POST.getlist("action")[0],
            },
        )

    def _run_each(self, request, queryset, verb: str, action) -> None:
        """逐条执行, 成功与失败分别报出来.

        批量动作里一条失败不该连累其余 —— 选中十条只有一条状态不对时, 管理员要看到
        另外九条成了、这一条为什么没成.
        """
        done, failed = 0, []
        for refund in queryset:
            try:
                action(refund)
            except OrderServiceError as exc:
                failed.append(f"{refund} —— {exc}")
            else:
                done += 1

        if done:
            self.message_user(request, f"已{verb} {done} 笔退款.")
        for reason in failed:
            self.message_user(request, f"未处理: {reason}", messages.ERROR)
