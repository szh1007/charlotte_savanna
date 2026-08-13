"""Order business logic — create, pay, cancel, ship, receive."""

from django.db import transaction
from django.utils import timezone

from .models import CartItem, Order, OrderItem, Product, ShippingAddress
from .utils import generate_order_no


class OrderServiceError(Exception):
    """Base exception for order service."""


class InsufficientStockError(OrderServiceError):
    """Insufficient stock."""


class InvalidOrderStatusError(OrderServiceError):
    """Order status transition not allowed."""


class PaymentError(OrderServiceError):
    """Payment failed."""


def create_order(user, cart_item_ids, address_id):
    """Create order from cart items with stock deduction and snapshot.

    Args:
        user: authenticated user
        cart_item_ids: list of CartItem ids to include in order
        address_id: ShippingAddress id

    Returns:
        Order instance

    Raises:
        InsufficientStockError: if any product stock is insufficient
    """
    cart_items = CartItem.objects.filter(
        id__in=cart_item_ids, cart__user=user
    ).select_related("product")
    if not cart_items:
        raise OrderServiceError("Cart is empty")

    try:
        address = ShippingAddress.objects.get(id=address_id, user=user)
    except ShippingAddress.DoesNotExist:
        raise OrderServiceError("Invalid shipping address")

    with transaction.atomic():
        # Lock product rows to prevent oversell
        product_ids = [item.product_id for item in cart_items]
        products = list(Product.objects.select_for_update().filter(id__in=product_ids))
        product_map = {p.id: p for p in products}

        # Validate stock
        for item in cart_items:
            product = product_map.get(item.product_id)
            if product is None or not product.is_active:
                raise InsufficientStockError(
                    f"Product '{item.product.name}' is no longer available"
                )
            if item.quantity > product.stock:
                raise InsufficientStockError(
                    f"Insufficient stock for '{product.name}': "
                    f"requested {item.quantity}, available {product.stock}"
                )

        # Deduct stock
        for item in cart_items:
            product = product_map[item.product_id]
            product.stock -= item.quantity
            product.save(update_fields=["stock"])

        # Create order
        total = sum(item.quantity * item.product.price for item in cart_items)
        order_no = generate_order_no(user.id)
        order = Order.objects.create(
            order_no=order_no,
            user=user,
            status=Order.Status.PENDING,
            total_amount=total,
            shipping_address_snapshot={
                "receiver_name": address.receiver_name,
                "phone": address.phone,
                "province": address.province,
                "city": address.city,
                "district": address.district,
                "detail": address.detail,
            },
        )

        # Create order items (snapshot)
        for item in cart_items:
            OrderItem.objects.create(
                order=order,
                product=item.product,
                product_name=item.product.name,
                product_price=item.product.price,
                quantity=item.quantity,
                subtotal=item.quantity * item.product.price,
            )

        # Clear cart items
        cart_items.delete()

    return order


def pay_order(order, payment_password):
    """Pay an order.

    Args:
        order: Order instance
        payment_password: 6-digit payment password (raw)

    Raises:
        InvalidOrderStatusError: order not in pending status
        PaymentError: wrong password or password not set
    """
    if order.status != Order.Status.PENDING:
        raise InvalidOrderStatusError(f"Cannot pay order in '{order.status}' status")
    profile = order.user.minimall_profile
    if not profile.check_payment_password(payment_password):
        raise PaymentError("支付密码错误")

    if profile.balance < order.total_amount:
        raise PaymentError("余额不足")

    profile.balance -= order.total_amount
    profile.save(update_fields=["balance"])
    order.status = Order.Status.PAID
    order.paid_at = timezone.now()
    order.save(update_fields=["status", "paid_at", "updated_at"])


def cancel_order(order):
    """Cancel an order and restore stock.

    Args:
        order: Order instance

    Raises:
        InvalidOrderStatusError: order cannot be cancelled in current status
    """
    if order.status not in (Order.Status.PENDING, Order.Status.PAID):
        raise InvalidOrderStatusError(f"Cannot cancel order in '{order.status}' status")

    with transaction.atomic():
        product_ids = list(order.items.values_list("product_id", flat=True))
        products = list(Product.objects.select_for_update().filter(id__in=product_ids))
        product_map = {p.id: p for p in products}

        for item in order.items.all():
            product = product_map.get(item.product_id)
            if product:
                product.stock += item.quantity
                product.save(update_fields=["stock"])

        # 已付款的订单取消后退还金额
        if order.status == Order.Status.PAID:
            profile = order.user.minimall_profile
            profile.balance += order.total_amount
            profile.save(update_fields=["balance"])

        order.status = Order.Status.CANCELLED
        order.cancelled_at = timezone.now()
        order.save(update_fields=["status", "cancelled_at", "updated_at"])


def ship_order(order):
    """Mark order as shipped (admin action).

    Args:
        order: Order instance

    Raises:
        InvalidOrderStatusError: order not in paid status
    """
    if order.status != Order.Status.PAID:
        raise InvalidOrderStatusError(f"Cannot ship order in '{order.status}' status")
    order.status = Order.Status.SHIPPED
    order.shipped_at = timezone.now()
    order.save(update_fields=["status", "shipped_at", "updated_at"])


def receive_order(order):
    """Mark order as received by buyer.

    Args:
        order: Order instance

    Raises:
        InvalidOrderStatusError: order not in shipped status
    """
    if order.status != Order.Status.SHIPPED:
        raise InvalidOrderStatusError(
            f"Cannot receive order in '{order.status}' status"
        )
    order.status = Order.Status.RECEIVED
    order.received_at = timezone.now()
    order.save(update_fields=["status", "received_at", "updated_at"])


def complete_order(order):
    """Mark order as completed.

    Args:
        order: Order instance

    Raises:
        InvalidOrderStatusError: order not in received status
    """
    if order.status != Order.Status.RECEIVED:
        raise InvalidOrderStatusError(
            f"Cannot complete order in '{order.status}' status"
        )
    order.status = Order.Status.COMPLETED
    order.save(update_fields=["status", "updated_at"])
