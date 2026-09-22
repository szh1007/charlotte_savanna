"""面向 agent 的序列化契约 (CharApp 助手专用).

与网页用的 serializers.py 分开维护, 因为两者契约不同:

- 商品列表带上 `stock` —— 助手回答"还有货吗"必须给当前库存,
  而网页列表靠商品详情页兜底, 列表本身不含库存;
- 状态类字段同时给 code 与中文 label —— 助手直接说人话, 不做码值翻译;
- 金额一律渲染为 2 位小数字符串, 避免 float 精度与类型混用.

改这里的字段等于改 agent 契约, 消费方是 CharApp/minimall/.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    CartItem,
    Category,
    Order,
    OrderItem,
    Product,
    RefundRequest,
    ShippingAddress,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# 商品 / 分类
# ---------------------------------------------------------------------------


class AgentProductListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "price", "stock", "category_name"]


class AgentProductDetailSerializer(serializers.ModelSerializer):
    category_tree = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "price",
            "stock",
            "is_featured",
            "category_tree",
        ]

    def get_category_tree(self, obj):
        ancestors = obj.category.get_ancestors(include_self=True)
        return [{"id": c.id, "name": c.name, "slug": c.slug} for c in ancestors]


class AgentCategoryTreeSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "children"]

    def get_children(self, obj):
        qs = obj.children.filter(is_active=True)
        if qs.exists():
            return AgentCategoryTreeSerializer(qs, many=True).data
        return []


# ---------------------------------------------------------------------------
# 购物车
# ---------------------------------------------------------------------------


class AgentCartItemSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_slug = serializers.CharField(source="product.slug", read_only=True)
    product_price = serializers.DecimalField(
        source="product.price", max_digits=10, decimal_places=2, read_only=True
    )
    stock = serializers.IntegerField(source="product.stock", read_only=True)
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "product_id",
            "product_name",
            "product_slug",
            "product_price",
            "quantity",
            "stock",
            "subtotal",
        ]

    def get_subtotal(self, obj) -> str:
        return f"{obj.quantity * obj.product.price:.2f}"


def build_cart_payload(cart) -> dict:
    """组装购物车返回体.

    Args:
        cart: Cart 实例, 或 None (该买家从未加购过).

    Returns:
        空购物车形态固定, 调用方无需分支处理.
    """
    if cart is None:
        return {"items": [], "total_count": 0, "total_amount": "0.00"}
    items = list(cart.items.select_related("product").order_by("-added_at"))
    total_amount = sum((i.quantity * i.product.price for i in items), Decimal("0.00"))
    return {
        "items": AgentCartItemSerializer(items, many=True).data,
        "total_count": sum(i.quantity for i in items),
        "total_amount": f"{total_amount:.2f}",
    }


# ---------------------------------------------------------------------------
# 订单
# ---------------------------------------------------------------------------


class AgentOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["product_id", "product_name", "product_price", "quantity", "subtotal"]


class AgentOrderListSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_no",
            "status",
            "status_display",
            "total_amount",
            "item_count",
            "created_at",
        ]

    def get_item_count(self, obj) -> int:
        return obj.items.count()


class AgentOrderDetailSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    items = AgentOrderItemSerializer(many=True, read_only=True)
    status_timeline = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_no",
            "status",
            "status_display",
            "total_amount",
            "shipping_address_snapshot",
            "items",
            "status_timeline",
            "created_at",
            "paid_at",
            "shipped_at",
            "received_at",
            "cancelled_at",
        ]

    def get_status_timeline(self, obj):
        timeline = [{"status": "pending", "label": "下单", "time": obj.created_at}]
        for status_code, label, when in (
            ("paid", "付款", obj.paid_at),
            ("shipped", "发货", obj.shipped_at),
            ("received", "收货", obj.received_at),
            ("cancelled", "取消", obj.cancelled_at),
        ):
            if when:
                timeline.append({"status": status_code, "label": label, "time": when})
        return timeline


# ---------------------------------------------------------------------------
# 账户
# ---------------------------------------------------------------------------


class AgentProfileSerializer(serializers.ModelSerializer):
    phone = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "phone", "balance"]

    def get_phone(self, obj):
        profile = getattr(obj, "minimall_profile", None)
        return profile.phone if profile else None

    def get_balance(self, obj) -> str:
        profile = getattr(obj, "minimall_profile", None)
        return f"{profile.balance:.2f}" if profile else "0.00"


class AgentAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingAddress
        fields = [
            "id",
            "receiver_name",
            "phone",
            "province",
            "city",
            "district",
            "detail",
            "is_default",
        ]


# ---------------------------------------------------------------------------
# 写操作的请求体 (issue 11)
# ---------------------------------------------------------------------------
# 与只读那批同一条纪律: 这是 agent 的契约, 不复用买家面 serializers.py 那套
# (那边是按网页表单设计的, 改网页形状不应该动到助手).
#
# 只做「形状」校验 (字段在不在, 类型对不对) —— 业务规则一律留给 services.py:
# 库存够不够, 状态让不让, 余额够不够, 都由那边的锁内判据回答. 这里放开一条,
# 不等于那边也放开 (端点的校验永远不会是唯一那道).


class AgentCartItemAddSerializer(serializers.Serializer):
    """加购: `slug` + 数量 (默认 1)."""

    # 用 slug 不用 cart_item_id —— 商品标识在商品页/搜索结果/购物车返回体里到处
    # 都能看到, 而主键对模型是个没有语义的数字 (见 issue 11 的清单).
    slug = serializers.CharField()
    quantity = serializers.IntegerField(default=1, min_value=1)


class AgentCartItemUpdateSerializer(serializers.Serializer):
    """改数量; **0 = 拿掉这一条** (与 service 的哨兵值一致)."""

    quantity = serializers.IntegerField(min_value=0)


class AgentOrderCreateSerializer(serializers.Serializer):
    """下单: 不带 address_id 就用默认收货地址."""

    address_id = serializers.IntegerField(required=False, min_value=1)


class AgentRefundCreateSerializer(serializers.Serializer):
    """申请退款: 只给订单号 —— **不带金额** (金额由管理员批准时协商, PRD §4.5)."""

    order_no = serializers.CharField()


# ---------------------------------------------------------------------------
# 写操作的返回体
# ---------------------------------------------------------------------------


class AgentRefundSerializer(serializers.ModelSerializer):
    """一条退款申请 (申请完 / 列表里都用它).

    `admin_note` 一定要给: 故事 18 问「为什么被驳回」, 答案就写在这一栏.
    `amount` 可以是 null —— 还没批准时协商金额还没定.
    """

    order_no = serializers.CharField(source="order.order_no", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = RefundRequest
        fields = [
            "order_no",
            "status",
            "status_display",
            "amount",
            "admin_note",
            "created_at",
            "approved_at",
            "refunded_at",
            "rejected_at",
        ]


class AgentOrderCancelSerializer(AgentOrderDetailSerializer):
    """取消订单的回执: 订单详情 + 这次动作回滚了几件库存.

    `restocked_count` 是买家最关心的那件事之一 (「货退了吗」), 所以它必须出现在
    响应里, 别只回一句 ok. 改判之后取消**永远**回滚全部明细 (`cancel_order` 只认
    `pending`), 于是它就是整张订单的件数之和 —— 不再需要按付款时间戳去猜「哪几件
    是扣过的」.

    `balance_returned` 改判后**恒为 "0.00"**: 只有未付款的订单能取消, 那种单从没
    扣过钱 (取消不再有退还余额这一支). 留着一个恒 0 的字段是为了回执形状不变 ——
    它是 issue 11 定下的写端点契约, 客户端与用例都按这个形状解析, 为一个不存在的
    金额去改契约不划算.
    """

    balance_returned = serializers.SerializerMethodField()
    restocked_count = serializers.SerializerMethodField()

    class Meta(AgentOrderDetailSerializer.Meta):
        fields = [
            *AgentOrderDetailSerializer.Meta.fields,
            "balance_returned",
            "restocked_count",
        ]

    def get_balance_returned(self, obj) -> str:
        """退回余额的金额 —— 恒定 "0.00", 见类 docstring.

        改判之前这里按 `paid_at` 判 (已付款的取消要退钱), 那一支现在走不到了: 只有
        未付款的订单能取消, 而它从没扣过钱. 破例留下一个恒定的字段, 理由在类 docstring.
        """
        return "0.00"

    def get_restocked_count(self, obj) -> int:
        """这次回滚了几件库存 —— 数字来自订单明细 (它就是当初扣掉的那批)."""
        return sum(item.quantity for item in obj.items.all())
