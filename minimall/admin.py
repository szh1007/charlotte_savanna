from django.contrib import admin
from django.utils import timezone
from mptt.admin import MPTTModelAdmin

from .models import Category, Order, OrderItem, Product, ProductImage


@admin.register(Category)
class CategoryAdmin(MPTTModelAdmin):
    list_display = ["name", "slug", "is_active"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 3


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "price", "stock", "is_active", "created_at"]
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
class OrderAdmin(admin.ModelAdmin):
    list_display = ["order_no", "user", "status", "total_amount", "created_at"]
    list_filter = ["status"]
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

    @admin.action(description="Ship selected orders")
    def action_ship_orders(self, request, queryset):
        updated = queryset.filter(status=Order.Status.PAID).update(
            status=Order.Status.SHIPPED, shipped_at=timezone.now()
        )
        self.message_user(request, f"{updated} orders shipped.")
