"""商城业务逻辑: 购物车写操作, 订单生命周期, 退款域.

写路径 (加购/改量/移除/清空/下单/付款/取消/退款) 都会被并发调用 —— 买家点两下,
助手和页面同时动手, 都会撞在一起. 两条规矩:

1. **判据取锁内的值**: 调用方传进来的实例可能是几秒前读的. 会动到**钱与库存**的
   判断 (下单/付款/取消/退款) 一律在 `select_for_update` 之后重新读一次, 不信手里
   那份; `pay_order` / `cancel_order` / 四个退款函数因此**返回**锁内那份实例, 调用
   方要拿返回值去渲染, 传进来的那份不作数. (`ship_order` / `receive_order` /
   `complete_order` 不在这条里: 它们只推一步状态, 不碰钱与库存, 重复执行最多是
   时间戳被再写一次.)
2. **行锁顺序固定** `Order → RefundRequest → Product → CartItem → Profile`: 要加锁
   的函数都按同一顺序, 两笔写操作互相等待时不会绕成环.

只读路径 (页面展示, agent 的只读端点) 不在这里 —— 它们用不着锁.

退款域跟订单函数放在同一份文件里, 是因为两者的状态机**耦合在四个转换点上**
(ADR-0004): 拆成两个模块, 就没有一个地方能看全这条状态机."""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Cart,
    CartItem,
    Order,
    OrderItem,
    Product,
    Profile,
    RefundRequest,
    ShippingAddress,
)
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


class InsufficientBalanceError(PaymentError):
    """余额不够付这一单 (充值就能解决, 与「密码错了」不是一回事)."""


class EmptyCartError(OrderServiceError):
    """购物车里没有可下单的商品."""


class InvalidAddressError(OrderServiceError):
    """收货地址不存在, 或不属于这个买家 (两种情况一律当成不存在)."""


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


class RefundNotAllowedError(OrderServiceError):
    """这张订单现在的状态不能申请退款 (只有钱已经出去的四个状态能提)."""


class RefundAlreadyInProgressError(OrderServiceError):
    """这张订单已有一笔进行中的退款申请 (`requested` / `approved`)."""


class InvalidRefundStatusError(OrderServiceError):
    """退款申请的状态不允许这个动作 (批准 / 打款 / 驳回 各有前置状态)."""


class InvalidRefundAmountError(OrderServiceError):
    """退款金额不在 `(0, 订单总额]` 区间内."""


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
        EmptyCartError: 购物车里没有可下单的商品.
        InvalidAddressError: 地址不存在, 或不属于这个买家.
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
        raise EmptyCartError("Cart is empty")

    try:
        address = ShippingAddress.objects.get(id=address_id, user=user)
    except ShippingAddress.DoesNotExist:
        raise InvalidAddressError("Invalid shipping address")

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
            raise EmptyCartError("Cart is empty")

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
            raise InsufficientBalanceError("余额不足")

        profile.balance -= locked.total_amount
        profile.save(update_fields=["balance"])
        locked.status = Order.Status.PAID
        locked.paid_at = timezone.now()
        locked.save(update_fields=["status", "paid_at", "updated_at"])

    return locked


def _restock_items(order) -> None:
    """把这张订单的明细逐件加回库存 (取消与退款打款共用).

    抽出来**不是**为了 DRY (只有两处, 够不上 CLAUDE.md 那条「重复 ≥ 3 次才抽取」的
    线), 而是为了保住一条可审计的性质: 全仓写库存只有这一条路径 —— 加库存流水、
    对账、预警都改这一处.

    调用方必须已在事务里, 并且已经锁住订单行 (锁序 `Order → Product → ...`); 这里
    只负责锁商品行并把件数加回去.
    """
    items = list(order.items.all())
    product_ids = [item.product_id for item in items]
    products = list(Product.objects.select_for_update().filter(id__in=product_ids))
    product_map = {p.id: p for p in products}

    for item in items:
        product = product_map.get(item.product_id)
        if product:
            product.stock += item.quantity
            product.save(update_fields=["stock"])


def cancel_order(order) -> Order:
    """取消订单并回滚库存 —— **只有未付款 (`pending`) 的订单能取消**.

    付款之后一律走退款 (2026-09-22 改判): 判据从「订单处于什么状态」换成「买家说的
    是哪个动词」—— 买家说「取消」才取消, 说「退款」就走退款申请. 取消是买家单方、
    即刻生效; 退款要管理员审批. 因此这里没有「退还余额」那一支: `pending` 的单从没
    扣过钱.

    Args:
        order: Order instance, used for locating only (状态一律以锁内的为准)

    Returns:
        The locked instance after cancellation (the passed-in ``order`` is
        left untouched — see pay_order).

    Raises:
        InvalidOrderStatusError: order cannot be cancelled in current status
    """
    with transaction.atomic():
        # 锁内 re-fetch: 传入的实例可能还停在付款前, 拿它判状态就会把已付款的订单
        # 当未付款的取消掉 (那种单该走退款, 货与钱的去向都不一样)
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status != Order.Status.PENDING:
            raise InvalidOrderStatusError(
                f"Cannot cancel order in '{locked.status}' status"
            )

        _restock_items(locked)

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


# ---------------------------------------------------------------------------
# 退款 (申请 / 批准 / 打款 / 驳回)
# ---------------------------------------------------------------------------
# 两个状态机的四个转换点 (ADR-0004): RefundRequest.status 与 Order.status 必须
# 一起变. 四个转换一律走下面四个函数, 别在别处直接改订单或退款单的状态.
#
# **库存回滚以发货为界** (2026-09-22 改判, 原「退款一律不动库存」作废): 货还没出去
# 就回滚, 出去了就不回滚 —— 与走取消还是走退款无关. 判据是申请那一刻的快照
# (`RefundRequest.order_status_before`), 所以回滚落在**打款**那一步, 不在申请时:
# 申请时货还在这一单手上, 提前放回库存等于让同一单同时占着货和钱.

# 能发起退款的订单状态: 钱已经出去的那四个. `pending` 还没扣钱 —— 走取消就行.
REFUNDABLE_STATUSES = (
    Order.Status.PAID,
    Order.Status.SHIPPED,
    Order.Status.RECEIVED,
    Order.Status.COMPLETED,
)


def request_refund(order) -> RefundRequest:
    """买家申请退款: 建一条申请, 订单置 `refunding`.

    与 `cancel_order` 的差别: 取消是买家单方、无需审批、即刻生效, 而且**只有未付款
    的订单能取消**; 付款之后一律走这条路 (要管理员批准, 退多少由管理员协商).

    退款流程进行中的订单**不允许发货** —— 现状已经如此, 两处都拦 (`action_ship_orders`
    只扫 `paid`, `ship_order` 也只认 `paid`), 写在这里免得以后被当成漏洞修: 货一旦
    发出去, 申请时取的那份 `order_status_before` 快照就与事实脱节, 打款那步会按
    「还没出去」把库存加回来.

    判据取锁内重新读的值 (与订单那三个函数同一条规矩): 调用方手里那份可能还停在
    `paid`. 锁订单行同时兼作**防并发双提** —— 两个请求同时申请时会在这行上排队,
    后到的那个在锁内看得见前一条申请, 于是被拒.

    Args:
        order: 订单实例, 只用来定位 (状态一律以锁内的为准).

    Returns:
        新建的申请 (`requested`, 金额为空 —— 金额在批准那步协商).

    Raises:
        RefundAlreadyInProgressError: 这单已有一条 `requested` / `approved` 的申请.
        RefundNotAllowedError: 订单状态不在 `REFUNDABLE_STATUSES` 里.
    """
    with transaction.atomic():
        # 订单行就是「同一订单只允许一条进行中申请」的那把锁
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if RefundRequest.objects.filter(
            order=locked, status__in=RefundRequest.ACTIVE_STATUSES
        ).exists():
            raise RefundAlreadyInProgressError(
                f"订单 {locked.order_no} 已有一笔进行中的退款申请"
            )
        if locked.status not in REFUNDABLE_STATUSES:
            raise RefundNotAllowedError(
                f"订单 {locked.order_no} 状态为 '{locked.status}', 不能申请退款"
            )

        refund = RefundRequest.objects.create(
            order=locked,
            status=RefundRequest.Status.REQUESTED,
            order_status_before=locked.status,
        )
        locked.status = Order.Status.REFUNDING
        locked.save(update_fields=["status", "updated_at"])

    return refund


def _locked_refund(
    refund, *, expected: RefundRequest.Status, action: str
) -> tuple[Order, RefundRequest]:
    """审批三个动作共用的前奏: 按统一锁序锁住订单行与退款单, 并校验退款单状态.

    锁序 (`Order → RefundRequest → ...`, 见模块 docstring) 在这里只写一遍 —— 三个
    函数各抄一遍的话, 哪天有人给其中一个调了顺序, 死锁就会在并发下悄悄回来. 即便
    动作不改订单状态 (批准就是), 也照样先锁它: 顺序一致比省一把锁重要.

    Args:
        refund: 退款申请实例, 只用来定位.
        expected: 这个动作要求的退款单状态.
        action: 动作名 (批准 / 打款 / 驳回), 只用来拼错误消息.

    Returns:
        (锁内的订单, 锁内的退款单).

    Raises:
        InvalidRefundStatusError: 退款单不在 `expected` 状态.
    """
    locked_order = Order.objects.select_for_update().get(pk=refund.order_id)
    locked = RefundRequest.objects.select_for_update().get(pk=refund.pk)
    if locked.status != expected:
        raise InvalidRefundStatusError(
            f"退款申请处于 '{locked.status}', 只有{expected.label}的能{action}"
        )
    return locked_order, locked


def approve_refund(refund, *, amount: Decimal, note: str = "") -> RefundRequest:
    """管理员批准退款: 把协商金额定死在这一步; **订单状态不变** (仍是 `refunding`).

    批准与打款分开是故意的 (PRD §4.5): 生产环境里两者之间常隔着支付网关, 而且这个
    中间态正好是「暂停等人工审批」的验证场. 金额在批准时定, 钱在打款时出.

    Args:
        refund: 退款申请实例, 只用来定位.
        amount: 实际退多少 (协商金额), 必须 `0 < amount <= order.total_amount`.
        note: 管理员备注, 这里写的是协商结果.

    Returns:
        锁内那份申请 (调用方传进来的那份不作数, 同 `pay_order`).

    Raises:
        InvalidRefundStatusError: 只有 `requested` 能批准.
        InvalidRefundAmountError: 金额不在 `(0, 订单总额]` 区间内.
    """
    with transaction.atomic():
        locked_order, locked = _locked_refund(
            refund, expected=RefundRequest.Status.REQUESTED, action="批准"
        )
        if not 0 < amount <= locked_order.total_amount:
            raise InvalidRefundAmountError(
                f"退款金额 {amount} 不在 (0, 订单总额 {locked_order.total_amount}] 之内"
            )

        locked.status = RefundRequest.Status.APPROVED
        locked.amount = amount
        locked.admin_note = note
        locked.approved_at = timezone.now()
        locked.save(update_fields=["status", "amount", "admin_note", "approved_at"])

    return locked


def settle_refund(refund) -> RefundRequest:
    """管理员打款: 余额加上协商金额, 订单置 `refunded` 并写 `Order.refunded_at`.

    出账金额只认批准时定下的 `refund.amount`, 不是订单总额 —— 部分退款靠的就是这两
    者的差 (申请全退 100, 协商退 70, 出账就是 70).

    **货没出去就把库存加回来**: 判据是申请那一刻的快照 `order_status_before` —— 它是
    `paid` (还没发货) 才回滚, `shipped` / `received` / `completed` 一律不动. 用快照
    而不是 `shipped_at` 推断, 与 `reject_refund` 恢复状态是同一条纪律.

    回滚落在打款这步而不在申请时: 申请时货还在这一单手上, 提前放回库存等于让同一单
    同时占着货和钱.

    锁序 `Order → RefundRequest → Product → Profile` 与 `cancel_order` 那条一致 ——
    改判之后两条路径都会动库存, 顺序不一致就是并发退款与取消撞在一起时的死锁.

    Args:
        refund: 退款申请实例, 只用来定位.

    Returns:
        锁内那份申请.

    Raises:
        InvalidRefundStatusError: 只有 `approved` 能打款.
    """
    with transaction.atomic():
        locked_order, locked = _locked_refund(
            refund, expected=RefundRequest.Status.APPROVED, action="打款"
        )

        if locked.order_status_before == Order.Status.PAID:
            _restock_items(locked_order)

        now = timezone.now()
        profile = Profile.objects.select_for_update().get(user_id=locked_order.user_id)
        profile.balance += locked.amount
        profile.save(update_fields=["balance"])

        locked_order.status = Order.Status.REFUNDED
        locked_order.refunded_at = now
        locked_order.save(update_fields=["status", "refunded_at", "updated_at"])

        locked.status = RefundRequest.Status.REFUNDED
        locked.refunded_at = now
        locked.save(update_fields=["status", "refunded_at"])

    return locked


def reject_refund(refund, *, note: str = "") -> RefundRequest:
    """管理员驳回: 申请置 `rejected`, 订单**恢复**申请前的状态.

    恢复而不是推到某个终点: 驳回意味着「这笔退款不成立」, 订单该继续正常流转 (该
    发货发货, 该收货收货). 推到 `cancelled` 是错的 —— 取消是买家终止订单, 驳回不是.
    恢复取的是申请那一刻的快照 (`order_status_before`), 不靠时间戳推断.

    库存不用补偿: 回滚只发生在打款那一步, 申请与驳回都不动它.

    驳回后可以再提一条新申请, 所以「同一时刻只有一条」是应用层校验, 不是唯一约束.

    Args:
        refund: 退款申请实例, 只用来定位.
        note: 管理员备注, 这里写的是驳回原因 (故事 18 要拿它回答买家).

    Returns:
        锁内那份申请.

    Raises:
        InvalidRefundStatusError: 只有 `requested` 能驳回.
    """
    with transaction.atomic():
        locked_order, locked = _locked_refund(
            refund, expected=RefundRequest.Status.REQUESTED, action="驳回"
        )

        locked.status = RefundRequest.Status.REJECTED
        locked.admin_note = note
        locked.rejected_at = timezone.now()
        locked.save(update_fields=["status", "admin_note", "rejected_at"])

        locked_order.status = locked.order_status_before
        locked_order.save(update_fields=["status", "updated_at"])

    return locked
