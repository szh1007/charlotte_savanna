import contextlib
import os

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from mptt.models import MPTTModel, TreeForeignKey


def _ts():
    return timezone.now().strftime("%Y%m%d%H%M%S")


def avatar_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1]
    return f"app/minimall/uploads/avatars/user_{instance.user_id}_{_ts()}{ext}"


def product_image_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1]
    return (
        f"app/minimall/uploads/products/"
        f"product_{instance.product_id}_{instance.sort_order}_{_ts()}{ext}"
    )


class Profile(models.Model):
    """买家扩展信息 - 与 auth_user 通过 OneToOne 关联.

    is_staff 区分管理员: auth_user.is_staff=True -> 管理员
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="minimall_profile",
        verbose_name="用户",
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name="手机号",
    )
    avatar = models.ImageField(
        upload_to=avatar_upload_to,
        blank=True,
        null=True,
        verbose_name="头像",
    )
    payment_password = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        verbose_name="支付密码",
        help_text="6 位数字支付密码, 以哈希存储",
    )

    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="账户余额",
    )
    avatar_version = models.PositiveIntegerField(default=1, verbose_name="头像版本号")
    avatar_updated_at = models.DateTimeField(
        null=True, blank=True, verbose_name="头像更新时间"
    )

    class Meta:
        db_table = "minimall_profile"
        verbose_name = "用户-扩展"
        verbose_name_plural = verbose_name

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._old_avatar_name = self.avatar.name if self.pk else None

    def set_payment_password(self, raw_password: str) -> None:
        self.payment_password = make_password(raw_password)

    def check_payment_password(self, raw_password: str) -> bool:
        if not self.payment_password:
            return False
        return check_password(raw_password, self.payment_password)

    def save(self, *args, **kwargs):
        if self.pk and self._old_avatar_name:
            new_name = self.avatar.name or ""
            if new_name != self._old_avatar_name:
                with contextlib.suppress(Exception):
                    self.avatar.storage.delete(self._old_avatar_name)
                self.avatar_version = self.avatar_version + 1
                self.avatar_updated_at = timezone.now()
        super().save(*args, **kwargs)
        self._old_avatar_name = self.avatar.name if self.avatar else None


class Category(MPTTModel):
    """商品分类, 支持无限层级.

    通过 django-mptt 实现树形结构.
    """

    name = models.CharField(max_length=100, verbose_name="分类名称")
    slug = models.SlugField(max_length=100, unique=True, verbose_name="URL 别名")
    parent = TreeForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="上级分类",
    )
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序(越小越靠前)")

    class MPTTMeta:
        order_insertion_by = ["sort_order", "name"]

    class Meta:
        db_table = "minimall_category"
        verbose_name = "类别"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


class Product(models.Model):
    """商品."""

    name = models.CharField(max_length=200, verbose_name="商品名称")
    slug = models.SlugField(
        max_length=200, unique=True, blank=True, verbose_name="URL 别名"
    )
    description = models.TextField(blank=True, default="", verbose_name="商品描述")
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="所属分类",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="价格")
    stock = models.PositiveIntegerField(default=0, verbose_name="库存")
    is_active = models.BooleanField(default=True, verbose_name="是否上架")
    is_featured = models.BooleanField(default=False, verbose_name="是否推荐")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序(越小越靠前)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "minimall_product"
        verbose_name = "商品"
        verbose_name_plural = verbose_name
        ordering = ["sort_order", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ProductImage(models.Model):
    """商品图片."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="商品",
    )
    image = models.ImageField(upload_to=product_image_upload_to, verbose_name="图片")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="排序")
    image_version = models.PositiveIntegerField(default=1, verbose_name="图片版本号")
    image_updated_at = models.DateTimeField(
        null=True, blank=True, verbose_name="图片更新时间"
    )

    class Meta:
        db_table = "minimall_product_image"
        verbose_name = "商品图片"
        verbose_name_plural = verbose_name
        ordering = ["sort_order"]

    def __str__(self):
        return f"{self.product.name} - Image {self.sort_order}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._old_sort_order = self.sort_order if self.pk else None

    def save(self, *args, **kwargs):
        # 排序变更时重命名磁盘文件
        if (
            self.pk
            and self._old_sort_order is not None
            and self.sort_order != self._old_sort_order
            and self.image
            and self.image.name
        ):
            try:
                old_path = self.image.storage.path(self.image.name)
                ext = os.path.splitext(self.image.name)[1]
                new_rel = (
                    f"app/minimall/uploads/products/"
                    f"product_{self.product_id}_{self.sort_order}_{_ts()}{ext}"
                )
                new_path = self.image.storage.path(new_rel)
                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                os.rename(old_path, new_path)
                self.image.name = new_rel
                self.image_version = self.image_version + 1
                self.image_updated_at = timezone.now()
            except Exception:
                pass
        super().save(*args, **kwargs)
        self._old_sort_order = self.sort_order

    def delete(self, *args, **kwargs):
        # 删除数据库记录时同步删除磁盘文件
        if self.image and self.image.name:
            with contextlib.suppress(Exception):
                self.image.storage.delete(self.image.name)
        super().delete(*args, **kwargs)


class Cart(models.Model):
    """购物车 — 一个用户一个."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart",
        verbose_name="用户",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "minimall_cart"
        verbose_name = "购物车"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"Cart of {self.user.username}"


class CartItem(models.Model):
    """购物车条目 — 同一购物车同一商品只有一行."""

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="购物车",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name="商品",
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name="数量")
    added_at = models.DateTimeField(auto_now_add=True, verbose_name="添加时间")

    class Meta:
        db_table = "minimall_cart_item"
        verbose_name = "购物车条目"
        verbose_name_plural = verbose_name
        unique_together = ["cart", "product"]

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"


class ShippingAddress(models.Model):
    """收货地址."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addresses",
        verbose_name="用户",
    )
    receiver_name = models.CharField(max_length=100, verbose_name="收货人")
    phone = models.CharField(max_length=20, verbose_name="联系电话")
    province = models.CharField(max_length=50, verbose_name="省")
    city = models.CharField(max_length=50, verbose_name="市")
    district = models.CharField(max_length=50, verbose_name="区")
    detail = models.TextField(verbose_name="详细地址")
    is_default = models.BooleanField(default=False, verbose_name="默认地址")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "minimall_shipping_address"
        verbose_name = "收货地址"
        verbose_name_plural = verbose_name
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.receiver_name} - {self.detail[:20]}"


class Order(models.Model):
    """订单."""

    class Status(models.TextChoices):
        PENDING = "pending", "待付款"
        PAID = "paid", "已付款"
        SHIPPED = "shipped", "已发货"
        RECEIVED = "received", "已收货"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"
        REFUNDED = "refunded", "已退款"

    order_no = models.CharField(max_length=32, unique=True, verbose_name="订单号")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="用户",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="状态",
    )
    total_amount = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="总金额"
    )
    shipping_address_snapshot = models.JSONField(verbose_name="收货地址快照")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="付款时间")
    shipped_at = models.DateTimeField(null=True, blank=True, verbose_name="发货时间")
    received_at = models.DateTimeField(null=True, blank=True, verbose_name="收货时间")
    cancelled_at = models.DateTimeField(null=True, blank=True, verbose_name="取消时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "minimall_order"
        verbose_name = "订单"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_no


class OrderItem(models.Model):
    """Order item — snapshot of product at order time."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="订单",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        verbose_name="商品",
    )
    product_name = models.CharField(max_length=200, verbose_name="商品名称")
    product_price = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="商品单价"
    )
    quantity = models.PositiveIntegerField(verbose_name="数量")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="小计")

    class Meta:
        db_table = "minimall_order_item"
        verbose_name = "订单明细"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.product_name} x{self.quantity}"
