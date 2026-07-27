import hashlib
import json

from django.contrib.auth import get_user_model, login, logout
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .cache import (
    get_cached_category_tree,
    get_cached_product_detail,
    get_cached_product_list,
)
from .filters import ProductFilter
from .models import Cart, CartItem, Order, Product, ShippingAddress
from .serializers import (
    AddToCartSerializer,
    CartItemSerializer,
    CartSerializer,
    CreateOrderSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    PayOrderSerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    ProfileUpdateSerializer,
    ShippingAddressSerializer,
    UpdateCartItemSerializer,
    UserLoginSerializer,
    UserMeSerializer,
    UserRegisterSerializer,
)
from .services import (
    InvalidOrderStatusError,
    OrderServiceError,
    PaymentError,
    cancel_order,
    complete_order,
    create_order,
    pay_order,
    receive_order,
)

User = get_user_model()


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserRegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                {"id": user.id, "username": user.username, "email": user.email},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserLoginSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            login(request, user)
            return Response({"id": user.id, "username": user.username, "email": user.email})
        return Response(serializer.errors, status=status.HTTP_401_UNAUTHORIZED)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserMeSerializer(request.user)
        return Response(serializer.data)

    def patch(self, request):
        profile = request.user.minimall_profile
        serializer = ProfileUpdateSerializer(profile, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(UserMeSerializer(request.user).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        old = request.data.get("old_password", "")
        new = request.data.get("new_password", "")
        if not request.user.check_password(old):
            return Response({"detail": "旧密码错误"}, status=status.HTTP_400_BAD_REQUEST)
        if len(new) < 8:
            return Response({"detail": "新密码至少8位"}, status=status.HTTP_400_BAD_REQUEST)
        request.user.set_password(new)
        request.user.save()
        return Response({"detail": "ok"})


class RechargeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from decimal import Decimal, InvalidOperation

        amount = request.data.get("amount", 0)
        try:
            amount = Decimal(str(amount))
        except InvalidOperation:
            return Response({"detail": "请输入有效金额"}, status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return Response({"detail": "金额必须大于0"}, status=status.HTTP_400_BAD_REQUEST)
        profile = request.user.minimall_profile
        profile.balance += amount
        profile.save(update_fields=["balance"])
        serializer = UserMeSerializer(request.user)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


class CategoryTreeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(get_cached_category_tree())


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------


class ProductListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        params_str = json.dumps(request.query_params, sort_keys=True)
        params_hash = hashlib.md5(params_str.encode()).hexdigest()

        def load():
            qs = (
                Product.objects.filter(is_active=True)
                .select_related("category")
                .prefetch_related("images")
            )
            filterset = ProductFilter(request.query_params, queryset=qs)
            if filterset.is_valid():
                qs = filterset.qs
            page_size = int(request.query_params.get("page_size", 20))
            from django.core.paginator import Paginator

            paginator = Paginator(qs, page_size)
            page_number = int(request.query_params.get("page", 1))
            page = paginator.get_page(page_number)
            serializer = ProductListSerializer(
                page.object_list, many=True, context={"request": request}
            )
            return {
                "count": paginator.count,
                "page": page.number,
                "page_size": page_size,
                "total_pages": paginator.num_pages,
                "results": serializer.data,
            }

        result = get_cached_product_list(params_hash, load)
        return Response(result)


class ProductDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        def load():
            product = (
                Product.objects.filter(slug=slug, is_active=True).prefetch_related("images").first()
            )
            if product is None:
                return None
            return ProductDetailSerializer(product, context={"request": request}).data

        data = get_cached_product_detail(slug, load)
        if data is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(data)


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------


class CartView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_cart(self, user):
        cart, _ = Cart.objects.get_or_create(user=user)
        return cart

    def get(self, request):
        cart = self._get_cart(request.user)
        return Response(CartSerializer(cart, context={"request": request}).data)


class AddCartItemView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AddToCartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        product_id = serializer.validated_data["product_id"]
        quantity = serializer.validated_data["quantity"]

        try:
            product = Product.objects.get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            return Response({"detail": "Product not found"}, status=status.HTTP_404_NOT_FOUND)

        if quantity > product.stock:
            quantity = product.stock

        cart, _ = Cart.objects.get_or_create(user=request.user)
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"quantity": quantity},
        )
        if not created:
            cart_item.quantity = min(cart_item.quantity + quantity, product.stock)
            cart_item.save()

        return Response(
            CartItemSerializer(cart_item, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class UpdateCartItemView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, cart_item_id):
        cart_item = get_object_or_404(CartItem, id=cart_item_id, cart__user=request.user)
        serializer = UpdateCartItemSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        quantity = serializer.validated_data["quantity"]
        if quantity == 0:
            cart_item.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        cart_item.quantity = min(quantity, cart_item.product.stock)
        cart_item.save()
        return Response(CartItemSerializer(cart_item, context={"request": request}).data)


class DeleteCartItemView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, cart_item_id):
        cart_item = get_object_or_404(CartItem, id=cart_item_id, cart__user=request.user)
        cart_item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClearCartView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        CartItem.objects.filter(cart__user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Shipping Address
# ---------------------------------------------------------------------------


class AddressListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        addresses = request.user.addresses.all()
        return Response(ShippingAddressSerializer(addresses, many=True).data)

    def post(self, request):
        serializer = ShippingAddressSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        if serializer.validated_data.get("is_default"):
            request.user.addresses.update(is_default=False)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AddressDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_address(self, user, address_id):
        return get_object_or_404(ShippingAddress, id=address_id, user=user)

    def patch(self, request, address_id):
        address = self._get_address(request.user, address_id)
        serializer = ShippingAddressSerializer(address, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        if serializer.validated_data.get("is_default"):
            request.user.addresses.exclude(id=address.id).update(is_default=False)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, address_id):
        address = self._get_address(request.user, address_id)
        if address.is_default:
            return Response(
                {"detail": "Cannot delete default address. Set another as default first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        address.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


class OrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        orders = Order.objects.filter(user=request.user).prefetch_related("items")
        from django.core.paginator import Paginator

        paginator = Paginator(orders, 20)
        page = paginator.get_page(int(request.query_params.get("page", 1)))
        serializer = OrderListSerializer(page.object_list, many=True)
        return Response(
            {
                "count": paginator.count,
                "page": page.number,
                "total_pages": paginator.num_pages,
                "results": serializer.data,
            }
        )

    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            order = create_order(
                request.user,
                serializer.validated_data["cart_item_ids"],
                serializer.validated_data["address_id"],
            )
        except OrderServiceError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            OrderDetailSerializer(order).data,
            status=status.HTTP_201_CREATED,
        )


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_order(self, user, order_no):
        return get_object_or_404(Order, order_no=order_no, user=user)

    def get(self, request, order_no):
        order = self._get_order(request.user, order_no)
        return Response(OrderDetailSerializer(order).data)


class OrderPayView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_no):
        order = get_object_or_404(Order, order_no=order_no, user=request.user)
        serializer = PayOrderSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            pay_order(order, serializer.validated_data["payment_password"])
        except PaymentError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except InvalidOrderStatusError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderDetailSerializer(order).data)


class OrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_no):
        order = get_object_or_404(Order, order_no=order_no, user=request.user)
        try:
            cancel_order(order)
        except InvalidOrderStatusError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderDetailSerializer(order).data)


class OrderReceiveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_no):
        order = get_object_or_404(Order, order_no=order_no, user=request.user)
        try:
            receive_order(order)
        except InvalidOrderStatusError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderDetailSerializer(order).data)


class OrderCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_no):
        order = get_object_or_404(Order, order_no=order_no, user=request.user)
        try:
            complete_order(order)
        except InvalidOrderStatusError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderDetailSerializer(order).data)
