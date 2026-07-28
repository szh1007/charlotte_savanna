from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError
from rest_framework import serializers

from .models import (
    Cart,
    CartItem,
    Category,
    Order,
    OrderItem,
    Product,
    ProductImage,
    Profile,
    ShippingAddress,
)

User = get_user_model()


class UserRegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    payment_password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, min_length=6, max_length=6
    )

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already exists")
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already exists")
        return value

    def create(self, validated_data):
        payment_password = validated_data.pop("payment_password", None)
        try:
            user = User.objects.create_user(**validated_data)
        except IntegrityError:
            raise serializers.ValidationError("Registration failed, please try again")
        profile = Profile.objects.create(user=user, balance=10000)
        if payment_password:
            profile.set_payment_password(payment_password)
            profile.save(update_fields=["payment_password"])
        return user


class UserLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError("Invalid username or password")
        if not user.is_active:
            raise serializers.ValidationError("Account is disabled")
        attrs["user"] = user
        return attrs


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ["phone", "avatar", "payment_password", "balance"]
        extra_kwargs = {"payment_password": {"write_only": True}}


class UserMeSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(source="minimall_profile", read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_staff",
            "date_joined",
            "profile",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


class CategoryTreeSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "children"]

    def get_children(self, obj):
        qs = obj.children.filter(is_active=True)
        if qs.exists():
            return CategoryTreeSerializer(qs, many=True).data
        return []


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "image", "sort_order"]


class ProductListSerializer(serializers.ModelSerializer):
    first_image = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "price", "first_image", "category_name"]

    def get_first_image(self, obj):
        img = obj.images.first()
        if img:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(img.image.url)
            return img.image.url
        return None


class ProductDetailSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    category_tree = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "category",
            "category_tree",
            "price",
            "stock",
            "is_active",
            "is_featured",
            "images",
            "created_at",
            "updated_at",
        ]

    def get_category_tree(self, obj):
        ancestors = obj.category.get_ancestors(include_self=True)
        return [{"id": c.id, "name": c.name, "slug": c.slug} for c in ancestors]


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------


class AddToCartSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(default=1, min_value=1)


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0)


class CartItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_slug = serializers.CharField(source="product.slug", read_only=True)
    product_price = serializers.DecimalField(
        source="product.price", max_digits=10, decimal_places=2, read_only=True
    )
    product_image = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product_id",
            "product_name",
            "product_slug",
            "product_price",
            "product_image",
            "quantity",
            "subtotal",
        ]

    def get_product_image(self, obj):
        img = obj.product.images.first()
        if img:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(img.image.url)
            return img.image.url
        return None

    def get_subtotal(self, obj):
        return obj.quantity * obj.product.price


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    total_amount = serializers.SerializerMethodField()
    total_count = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = ["id", "items", "total_amount", "total_count"]

    def get_total_amount(self, obj):
        return sum(item.quantity * item.product.price for item in obj.items.all())

    def get_total_count(self, obj):
        return sum(item.quantity for item in obj.items.all())


# ---------------------------------------------------------------------------
# Shipping Address
# ---------------------------------------------------------------------------


class ShippingAddressSerializer(serializers.ModelSerializer):
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
            "created_at",
        ]
        read_only_fields = ["id", "user", "created_at"]


class ProfileUpdateSerializer(serializers.ModelSerializer):
    payment_password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, min_length=6, max_length=6
    )

    class Meta:
        model = Profile
        fields = ["phone", "avatar", "payment_password"]
        extra_kwargs = {"phone": {"required": False}, "avatar": {"required": False}}

    def update(self, instance, validated_data):
        payment_password = validated_data.pop("payment_password", None)
        instance.phone = validated_data.get("phone", instance.phone)
        instance.avatar = validated_data.get("avatar", instance.avatar)
        if payment_password:
            instance.set_payment_password(payment_password)
        instance.save()
        return instance


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


class CreateOrderSerializer(serializers.Serializer):
    cart_item_ids = serializers.ListField(child=serializers.IntegerField(min_value=1))
    address_id = serializers.IntegerField(min_value=1)


class PayOrderSerializer(serializers.Serializer):
    payment_password = serializers.CharField(write_only=True, min_length=6, max_length=6)


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product_id",
            "product_name",
            "product_price",
            "quantity",
            "subtotal",
        ]


class OrderListSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["order_no", "status", "total_amount", "item_count", "created_at"]

    def get_item_count(self, obj):
        return obj.items.count()


class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    status_timeline = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_no",
            "status",
            "total_amount",
            "shipping_address_snapshot",
            "items",
            "status_timeline",
            "paid_at",
            "shipped_at",
            "received_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]

    def get_status_timeline(self, obj):
        timeline = [{"status": "pending", "label": "Order placed", "time": obj.created_at}]
        if obj.paid_at:
            timeline.append({"status": "paid", "label": "Paid", "time": obj.paid_at})
        if obj.shipped_at:
            timeline.append({"status": "shipped", "label": "Shipped", "time": obj.shipped_at})
        if obj.received_at:
            timeline.append({"status": "received", "label": "Received", "time": obj.received_at})
        if obj.cancelled_at:
            timeline.append({"status": "cancelled", "label": "Cancelled", "time": obj.cancelled_at})
        return timeline
