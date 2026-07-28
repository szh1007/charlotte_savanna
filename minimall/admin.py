import contextlib

from django.contrib import admin
from django.utils import timezone
from mptt.admin import MPTTModelAdmin

from .models import Category, Order, OrderItem, Product, ProductImage, Profile

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
                queryset = queryset.filter(**{f"{self._field_name}__gte": float(min_val)})
        if max_val:
            with contextlib.suppress(ValueError, TypeError):
                queryset = queryset.filter(**{f"{self._field_name}__lte": float(max_val)})
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
