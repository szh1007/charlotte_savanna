"""商城业务逻辑: 购物车写操作与订单生命周期.

写路径 (加购/改量/移除/清空/下单/付款/取消) 都会被并发调用 —— 买家点两下,
助手和页面同时动手, 都会撞在一起. 两条规矩:

1. **判据取锁内的值**: 调用方传进来的实例可能是几秒前读的. 会动到**钱与库存**的
   判断 (下单/付款/取消) 一律在 `select_for_update` 之后重新读一次, 不信手里那份;
   `pay_order` / `cancel_order` 因此**返回**锁内那份实例, 调用方要拿返回值去渲染,
   传进来的那份不作数. (`ship_order` / `receive_order` / `complete_order` 不在这条
   里: 它们只推一步状态, 不碰钱与库存, 重复执行最多是时间戳被再写一次.)
2. **行锁顺序固定** `Order → Product → CartItem → Profile`: 要加锁的函数都按同一
   顺序, 两笔写操作互相等待时不会绕成环.

只读路径 (页面展示, agent 的只读端点) 不在这里 —— 它们用不着锁."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Cart, CartItem, Order, OrderItem, Product, Profile, ShippingAddress
from .utils import generate_order_no

# 单个商品在购物车里的数量上限 (库存之外的第二道闸).
# 只有「库存」一条上限时, 一次请求就能把整批库存塞进车里; 上限是业务规则,
# 不是库存的替代品 (库存决定还能买多少, 上限决定一次让不让买这么多).
MAX_CART_ITEM_QUANTITY = 99

# 订单号撞车后换号重试的次数; 用尽则抛业务异常 (不让 IntegrityError 冒给买家)
ORDER_NO_MAX_ATTEMPTS = 3


class OrderServiceError(Exception):
    """Base exception for order service."""


class InsufficientStockError(OrderServiceError):
    """Insufficient stock."""


class InvalidOrderStatusError(OrderServiceError):
    """Order status transition not allowed."""


class PaymentError(OrderServiceError):
    """Payment failed."""


class ProductUnavailableError(OrderServiceError):
    """商品不存在或已下架 (页面回 404)."""


class OutOfStockError(OrderServiceError):
    """商品库存为 0 (页面回 400 「商品暂时缺货」).

    与 InsufficientStockError 不是一回事: 那个是「下单时买得比库存多」,
    发生在结算; 这个是「加购时它就已经没货了」.
    """


class CartItemNotFoundError(OrderServiceError):
    """购物车条目不存在, 或不属于这个买家 (两种情况一律当成不存在)."""


class OrderNumberConflictError(OrderServiceError):
    """订单号连续撞车, 重试次数用尽."""


def _cap(quantity: int, product: Product) -> int:
    """数量截断: 请求量, 库存, 单件上限三者取最小.

    截断而不是报错 —— 与页面从前的行为一致 (买家要 10 件而库存 5 件, 拿到的是
    5 件), 助手那边也一样, 它把截断后的数量念给买家听.
    """
    return min(quantity, product.stock, MAX_CART_ITEM_QUANTITY)


# ---------------------------------------------------------------------------
# 购物车 (4 个写操作: 页面与助手两个入口共用这一份)
# ---------------------------------------------------------------------------


def _locked_cart_item(user, cart_item_id: int) -> CartItem:
    """锁内取购物车条目 (改 / 删两个动作共用); 不存在或不属于这个买家一律抛.

    归属过滤 (`cart__user=user`) 与加锁写在一起: 页面与助手两个入口都要经过
    改 / 删, 谁都不会漏掉这一层; 别人的条目在这个过滤下等同于不存在.
    """
    item = (
        CartItem.objects.select_for_update()
        .filter(id=cart_item_id, cart__user=user)
        .first()
    )
    if item is None:
        raise CartItemNotFoundError(f"购物车条目 {cart_item_id} 不存在")
    return item


def add_to_cart(user, *, product_id: int, quantity: int) -> tuple[CartItem, bool]:
    """把商品加进购物车; 已在车里则累加数量, 并按库存与上限截断.

    全程持商品行的锁: 库存校验, 数量截断, 条目读写都在同一把锁下完成, 两个
    请求同时加购同一商品也不会互相覆盖 (丢更新).

    Args:
        user: 买家.
        product_id: 商品 id —— 调用方按自己的自然键解析 (页面用 id, 助手用 slug),
            归属与库存一律由这里重新校验, 不信调用方给的结论.
        quantity: 本次要加的数量.

    Returns:
        (条目, 是否新建): 视图据此决定回 201 还是 200.

    Raises:
        ProductUnavailableError: 商品不存在或已下架.
        OutOfStockError: 商品库存为 0.
    """
    with transaction.atomic():
        product = (
            Product.objects.select_for_update()
            .filter(id=product_id, is_active=True)
            .first()
        )
        if product is None:
            raise ProductUnavailableError(f"商品 {product_id} 不存在或已下架")
        if product.stock <= 0:
            raise OutOfStockError("商品暂时缺货")

        cart, _ = Cart.objects.get_or_create(user=user)
        item = (
            CartItem.objects.select_for_update()
            .filter(cart=cart, product=product)
            .first()
        )
        if item is None:
            return (
                CartItem.objects.create(
                    cart=cart, product=product, quantity=_cap(quantity, product)
                ),
                True,
            )

        item.quantity = _cap(item.quantity + quantity, product)
        item.save(update_fields=["quantity"])
        return item, False


def update_cart_item(user, *, cart_item_id: int, quantity: int) -> CartItem | None:
    """改购物车条目的数量; quantity == 0 表示移除该条目.

    Args:
        user: 买家.
        cart_item_id: 条目 id.
        quantity: 新的数量; 0 表示把这一条从车里拿掉.

    Raises:
        CartItemNotFoundError: 条目不存在, 或不属于这个买家.

    Returns:
        改后的条目; 条目被移除时返回 None (视图据此回 204).
    """
    with transaction.atomic():
        # 先看一眼条目在不在 (才知道该锁哪个商品); 这一读不算数, 结论以锁内的为准
        product_id = (
            CartItem.objects.filter(id=cart_item_id, cart__user=user)
            .values_list("product_id", flat=True)
            .first()
        )
        if product_id is None:
            raise CartItemNotFoundError(f"购物车条目 {cart_item_id} 不存在")

        # 先锁商品行, 再锁条目 —— 顺序与加购/下单一致, 见模块 docstring.
        # 数量上限要用**加锁的** product.stock, 不能用 item.product 的懒加载值.
        product = Product.objects.select_for_update().get(id=product_id)
        # 锁内重取: 两个请求同时改这一行时, 后到的那个拿到的是别人改完的值
        item = _locked_cart_item(user, cart_item_id)

        if quantity == 0:
            item.delete()
            return None

        item.quantity = _cap(quantity, product)
        item.save(update_fields=["quantity"])
        return item


def remove_cart_item(user, *, cart_item_id: int) -> None:
    """移除购物车条目.

    不锁商品行: 它不动库存 (与加购/下单不是同一类动作). 锁条目本身是为了让
    「两个请求同时删同一行」有确定结果 —— 后到的那个在锁内看到的是空, 回 404.

    Args:
        user: 买家.
        cart_item_id: 条目 id.

    Raises:
        CartItemNotFoundError: 条目不存在, 或不属于这个买家.
    """
    with transaction.atomic():
        _locked_cart_item(user, cart_item_id).delete()


def clear_cart(user) -> None:
    """清空购物车.

    单条 DELETE 本身就是原子的 (Django 的 queryset.delete() 自带事务), 不碰
    库存也就不需要商品行锁 —— 与买家自己的另一次加购交错时, 要么留下新加的
    那件, 要么没有, 两种结果都对.
    """
    CartItem.objects.filter(cart__user=user).delete()


# ---------------------------------------------------------------------------
# 订单
# ---------------------------------------------------------------------------


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
    # 事务外只读一次「要锁哪些商品」: 这次读不参与任何业务判断, 拿到的只是 id
    # 清单 —— 数量与价格等进了锁再读 (事务外那份实例可能已经被人改过价)
    product_ids = list(
        CartItem.objects.filter(id__in=cart_item_ids, cart__user=user).values_list(
            "product_id", flat=True
        )
    )
    if not product_ids:
        raise OrderServiceError("Cart is empty")

    try:
        address = ShippingAddress.objects.get(id=address_id, user=user)
    except ShippingAddress.DoesNotExist:
        raise OrderServiceError("Invalid shipping address")

    with transaction.atomic():
        # Lock product rows to prevent oversell
        products = list(Product.objects.select_for_update().filter(id__in=product_ids))
        product_map = {p.id: p for p in products}

        # 锁内重读购物车行: 数量取这一刻的值, 价格一律取加锁的 product_map
        cart_items = list(
            CartItem.objects.select_for_update().filter(
                id__in=cart_item_ids, cart__user=user
            )
        )
        if not cart_items:
            raise OrderServiceError("Cart is empty")

        # Validate stock
        for item in cart_items:
            product = product_map.get(item.product_id)
            if product is None:
                raise InsufficientStockError(
                    f"Product {item.product_id} is no longer available"
                )
            if not product.is_active:
                raise InsufficientStockError(
                    f"Product '{product.name}' is no longer available"
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
        total = sum(
            item.quantity * product_map[item.product_id].price for item in cart_items
        )
        order = _create_order_with_unique_no(
            user_id=user.id,
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
            product = product_map[item.product_id]
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                product_price=product.price,
                quantity=item.quantity,
                subtotal=item.quantity * product.price,
            )

        # Clear cart items
        CartItem.objects.filter(id__in=cart_item_ids, cart__user=user).delete()

    return order


def _create_order_with_unique_no(*, user_id: int, **fields) -> Order:
    """建订单; 订单号撞车就换一个号重试, 次数用尽抛 OrderNumberConflictError.

    重试必须包住**插入**这一步 —— 撞不撞车只有插进去那一刻才知道 (先查再插
    仍有竞态, 而 order_no 是唯一索引, 数据库才是判官).

    每次尝试套一层 atomic(): 撞车抛的 IntegrityError 只回滚这一次插入 (savepoint),
    外层事务已经锁好的库存与购物车行不受影响 —— 没有这层, 一次撞车会把整单
    连库存一起回滚掉.

    这次插入只可能撞 order_no 一条唯一约束 (user 是外键), 所以捕获 IntegrityError
    就是在捕获撞车.
    """
    for _ in range(ORDER_NO_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                return Order.objects.create(
                    order_no=generate_order_no(user_id),
                    user_id=user_id,
                    status=Order.Status.PENDING,
                    **fields,
                )
        except IntegrityError:
            continue

    raise OrderNumberConflictError(f"订单号连续 {ORDER_NO_MAX_ATTEMPTS} 次撞车, 请重试")


def pay_order(order, payment_password) -> Order:
    """Pay an order.

    判据与扣款都在锁内: 状态看锁内 re-fetch 的值, 扣款与改状态在同一个事务里
    (从前是两次独立 save —— 中途失败就是「钱扣了单没付」).

    Args:
        order: Order instance, used for locating only (状态一律以锁内的为准)
        payment_password: 6-digit payment password (raw)

    Returns:
        The locked instance after payment. The passed-in ``order`` is left
        untouched (its fields may be stale) — render the return value.

    Raises:
        InvalidOrderStatusError: order not in pending status
        PaymentError: wrong password or password not set
    """
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status != Order.Status.PENDING:
            raise InvalidOrderStatusError(
                f"Cannot pay order in '{locked.status}' status"
            )

        profile = Profile.objects.select_for_update().get(user_id=locked.user_id)
        if not profile.check_payment_password(payment_password):
            raise PaymentError("支付密码错误")

        if profile.balance < locked.total_amount:
            raise PaymentError("余额不足")

        profile.balance -= locked.total_amount
        profile.save(update_fields=["balance"])
        locked.status = Order.Status.PAID
        locked.paid_at = timezone.now()
        locked.save(update_fields=["status", "paid_at", "updated_at"])

    return locked


def cancel_order(order) -> Order:
    """Cancel an order and restore stock.

    Args:
        order: Order instance, used for locating only (状态一律以锁内的为准)

    Returns:
        The locked instance after cancellation (the passed-in ``order`` is
        left untouched — see pay_order).

    Raises:
        InvalidOrderStatusError: order cannot be cancelled in current status
    """
    with transaction.atomic():
        # 锁内 re-fetch: 传入的实例可能还停在发货前, 拿它判状态就会把已发货的
        # 订单当已付款的取消掉 (钱退了, 货却在路上)
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status not in (Order.Status.PENDING, Order.Status.PAID):
            raise InvalidOrderStatusError(
                f"Cannot cancel order in '{locked.status}' status"
            )

        items = list(locked.items.all())
        product_ids = [item.product_id for item in items]
        products = list(Product.objects.select_for_update().filter(id__in=product_ids))
        product_map = {p.id: p for p in products}

        for item in items:
            product = product_map.get(item.product_id)
            if product:
                product.stock += item.quantity
                product.save(update_fields=["stock"])

        # 已付款的订单取消后退还金额
        if locked.status == Order.Status.PAID:
            profile = Profile.objects.select_for_update().get(user_id=locked.user_id)
            profile.balance += locked.total_amount
            profile.save(update_fields=["balance"])

        locked.status = Order.Status.CANCELLED
        locked.cancelled_at = timezone.now()
        locked.save(update_fields=["status", "cancelled_at", "updated_at"])

    return locked


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
