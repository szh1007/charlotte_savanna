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


@admin.register(Profile)
class ProfileAdmin(BatchDeleteMixin, admin.ModelAdmin):
    list_display = ["user", "phone", "balance", "avatar_version"]
    list_filter = ["avatar_version"]
    search_fields = ["user__username", "phone"]
    readonly_fields = ["avatar_version", "avatar_updated_at"]
    exclude = ["payment_password"]
    ordering = ["-user__date_joined"]


@admin.register(Category)
class CategoryAdmin(BatchDeleteMixin, MPTTModelAdmin):
    list_display = ["name", "slug", "sort_order", "is_active"]
    list_editable = ["sort_order"]
    list_filter = ["is_active"]
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
    list_filter = ["category", "is_active", "is_featured"]
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
    list_filter = ["status", "created_at"]
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
