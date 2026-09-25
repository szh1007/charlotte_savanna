"""Agent 内部端点 (CharApp 助手专用): 只读 9 个 (issue 02) + 写 8 个 (issue 11)
+ 代付 1 个 (issue 35), 共 18 条.

前缀 /api/minimall/agent/, 认证 = X-Internal-Token (未配置即全拒, fail closed);
需要买家身份的端点再带 X-User-Id, 身份由调用方声明 (信任模型与生产化路径
见 CharApp/PRD.md §4.10).

商品数据一律直查数据库, 不走 Redis 缓存: 缓存里的库存可能已经过期十分钟,
而助手报出的"还剩 3 件"会被买家当作事实. 写操作上这条更硬 —— 用缓存判
"还能不能下单 / 还能不能取消"是会出真错的, 所以写端点也一律直查库走 service.

写端点**不做业务判断**: 规则全在 services.py (锁, 状态机, 库存, 余额), 这里只做
三件事 —— 认证身份, 把 slug / order_no 翻成 service 要的 id, 把业务异常翻成
错误体. 少一样东西都不在这里补 (补了就是第二份规则).
"""

from typing import NamedTuple

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .filters import ProductFilter
from .models import (
    Cart,
    CartItem,
    Category,
    Order,
    Product,
    RefundRequest,
    ShippingAddress,
)
from .permissions import IsInternalService
from .serializers_agent import (
    AgentAddressSerializer,
    AgentCartItemAddSerializer,
    AgentCartItemUpdateSerializer,
    AgentCategoryTreeSerializer,
    AgentOrderCancelSerializer,
    AgentOrderCreateSerializer,
    AgentOrderDetailSerializer,
    AgentOrderListSerializer,
    AgentOrderPaidSerializer,
    AgentOrderPaySerializer,
    AgentProductDetailSerializer,
    AgentProductListSerializer,
    AgentProfileSerializer,
    AgentRefundCreateSerializer,
    AgentRefundSerializer,
    build_cart_payload,
)
from .services import (
    CartItemNotFoundError,
    EmptyCartError,
    InsufficientBalanceError,
    InsufficientStockError,
    InvalidAddressError,
    InvalidOrderStatusError,
    InvalidRefundAmountError,
    InvalidRefundStatusError,
    OrderNumberConflictError,
    OrderServiceError,
    OutOfStockError,
    PaymentError,
    ProductUnavailableError,
    RefundAlreadyInProgressError,
    RefundNotAllowedError,
    add_to_cart,
    cancel_order,
    clear_cart,
    create_order,
    pay_order,
    remove_cart_item,
    request_refund,
    update_cart_item,
)

User = get_user_model()

# 页大小白名单 (与买家侧 views_buyer.py / views_html.py 同一份清单): 买家端只认
# 这 5 个值, agent 端点没有理由更宽 —— 工具层的 schema 已限死, 这里是端点的兜底
PAGE_SIZE_OPTIONS = (5, 10, 20, 50, 100)
DEFAULT_PAGE_SIZE = 20


def _paginate(queryset, request, serializer_class) -> dict:
    """统一分页返回体; 页码/页大小非法时回退默认值."""
    try:
        page_size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    if page_size not in PAGE_SIZE_OPTIONS:
        page_size = DEFAULT_PAGE_SIZE

    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.query_params.get("page", 1))
    return {
        "count": paginator.count,
        "page": page.number,
        "page_size": page_size,
        "total_pages": paginator.num_pages,
        "results": serializer_class(page.object_list, many=True).data,
    }


# ---------------------------------------------------------------------------
# 错误码: 写端点的拒绝长什么样 (issue 11)
# ---------------------------------------------------------------------------


class AgentErrorSpec(NamedTuple):
    """一个错误码的规定动作: 回哪个状态码 + 给模型看的中文说明."""

    status: int
    message: str


# 错误码总表 —— 这是**契约**: issue 12 的工具按码决定跟买家怎么说, 所以
# 「库存不足」与「状态不允许」必须是两个码, 混成一个工具就只能说「出错了」.
ERROR_CODES: dict[str, AgentErrorSpec] = {
    # 调用方契约问题 (字段缺了 / 类型不对)
    "invalid_request": AgentErrorSpec(400, "请求不合法"),
    # 商品
    "product_unavailable": AgentErrorSpec(404, "商品不存在或已下架"),
    "out_of_stock": AgentErrorSpec(409, "商品暂时缺货"),
    "insufficient_stock": AgentErrorSpec(409, "库存不足, 订单没有下成"),
    # 购物车
    "cart_item_not_found": AgentErrorSpec(404, "购物车里没有这件商品"),
    "cart_empty": AgentErrorSpec(409, "购物车是空的, 没有可下单的商品"),
    # 订单
    "order_not_found": AgentErrorSpec(404, "没有这笔订单"),
    "invalid_address": AgentErrorSpec(404, "收货地址不存在"),
    "no_default_address": AgentErrorSpec(
        400, "没有默认收货地址: 请先在页面上添加一个, 或指定一个已有地址"
    ),
    "invalid_order_status": AgentErrorSpec(409, "订单当前的状态不允许这个操作"),
    "order_no_conflict": AgentErrorSpec(409, "订单号连续冲突, 请重试"),
    # 钱与退款
    "payment_failed": AgentErrorSpec(400, "支付没有成功"),
    "insufficient_balance": AgentErrorSpec(400, "余额不足"),
    "refund_not_allowed": AgentErrorSpec(409, "这个订单现在的状态不能申请退款"),
    "refund_already_in_progress": AgentErrorSpec(409, "这笔订单已有一笔退款正在处理中"),
    "invalid_refund_status": AgentErrorSpec(409, "退款申请当前的状态不允许这个操作"),
    "invalid_refund_amount": AgentErrorSpec(400, "退款金额超出允许的区间"),
    # 兜底 (只可能来自没登记的新异常, 见下)
    "order_rejected": AgentErrorSpec(400, "这个操作没能完成"),
}

# service 异常 → 错误码. **每条业务异常都要登记** —— test_agent_write_api 里有一条
# 用例遍历 `OrderServiceError` 的子类来保证不漏; 漏了的那条会退化成兜底码,
# 上层就只能说「出错了」.
#
# 与钱有关的三个码在 issue 11 就立着了, 但当时只有"状态不允许"那条走得到 (取消一张
# 已付款的单). 「密码错」与「余额不够」要等 issue 35 的 `AgentOrderPayView` 才有入口
# —— 三条各自的下一步不同 (重输 / 充值 / 看状态), 工具层按码写文案.
EXCEPTION_CODES: dict[type[OrderServiceError], str] = {
    ProductUnavailableError: "product_unavailable",
    OutOfStockError: "out_of_stock",
    InsufficientStockError: "insufficient_stock",
    CartItemNotFoundError: "cart_item_not_found",
    EmptyCartError: "cart_empty",
    InvalidAddressError: "invalid_address",
    InvalidOrderStatusError: "invalid_order_status",
    OrderNumberConflictError: "order_no_conflict",
    PaymentError: "payment_failed",
    InsufficientBalanceError: "insufficient_balance",
    RefundNotAllowedError: "refund_not_allowed",
    RefundAlreadyInProgressError: "refund_already_in_progress",
    InvalidRefundStatusError: "invalid_refund_status",
    InvalidRefundAmountError: "invalid_refund_amount",
}

# 没登记过的业务异常的兜底码 (应当永远用不上)
FALLBACK_CODE = "order_rejected"


class AgentRefusalError(Exception):
    """端点自己判定的拒绝 (不来自业务异常的那几种: 请求体不合法 / 没这笔订单 等).

    用异常而不是就地返回错误响应, 是为了让处理器只写"正常那一路": 校验请求体
    (`_validated_data`) 与"取自己的订单"(`_own_order`) 都能在中途直接抛, 而翻成错误体
    的地方**只有一处** (`AgentEndpointView.handle_exception`).
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def _error_response(code: str, message: str | None = None) -> Response:
    """按码组装错误体 `{"error": {"code", "message"}}` (与 CharAgent 那边同一形状)."""
    spec = ERROR_CODES[code]
    return Response(
        {"error": {"code": code, "message": message or spec.message}},
        status=spec.status,
    )


def _resolve_buyer(request):
    """从 X-User-Id 头解析买家.

    缺失或非数字 → 400 (调用方契约问题);
    买家不存在或已禁用 → 404 (与商城既有归属校验一致, 不区分原因以防枚举).
    """
    raw = (request.META.get("HTTP_X_USER_ID") or "").strip()
    if not raw.isdigit():
        raise ValidationError({"detail": "缺少或非法的 X-User-Id 头"})
    try:
        return User.objects.get(pk=int(raw), is_active=True)
    except User.DoesNotExist:
        raise Http404


class AgentEndpointView(APIView):
    """Agent 内部端点基类: 只认内部令牌, 不用 Django 会话认证.

    需要买家身份的端点在方法里再调 `_resolve_buyer(request)` —— 身份始终
    按买家过滤, 不做对象级放行.

    业务异常与端点的拒绝都在 `handle_exception` 里翻成统一错误体, 所以处理器
    只管写正常那一路, 不用逐个 try/except. 9 个只读端点不抛业务异常, 这条对
    它们是惰性的 (它们一行没动).
    """

    authentication_classes = []
    permission_classes = [IsInternalService]

    def handle_exception(self, exc):
        """业务异常 / 端点的拒绝 → 错误体; 其余交给 DRF (404 仍是 404).

        错误码只在这一处翻译: 8 个写端点各自 try/except 一遍的话, 同一个异常在
        不同端点会被讲成不同的话, 而工具层是按码写文案的.
        """
        if isinstance(exc, AgentRefusalError):
            return _error_response(exc.code, exc.message)
        if isinstance(exc, OrderServiceError):
            return _error_response(EXCEPTION_CODES.get(type(exc), FALLBACK_CODE))
        return super().handle_exception(exc)

    def _validated_data(self, serializer_class) -> dict:
        """校验请求体并返回数据; 不合法直接抛 (字段级说明拼进 message).

        只校验形状 (字段在不在, 类型对不对) —— 业务规则一律留给 services.py,
        那边的锁内判据才是权威 (这里放开一条, 不等于那边也放开).
        """
        serializer = serializer_class(data=self.request.data)
        if serializer.is_valid():
            return serializer.validated_data
        detail = "; ".join(
            f"{name}: {' '.join(str(msg) for msg in messages)}"
            for name, messages in serializer.errors.items()
        )
        raise AgentRefusalError("invalid_request", detail)


# ---------------------------------------------------------------------------
# 商品 / 分类 (无需买家身份)
# ---------------------------------------------------------------------------


class AgentProductListView(AgentEndpointView):
    """商品列表: 关键词搜索 / 分类 / 价格区间 / 排序 / 分页."""

    def get(self, request):
        qs = Product.objects.filter(is_active=True).select_related("category")
        filterset = ProductFilter(request.query_params, queryset=qs)
        if filterset.is_valid():
            qs = filterset.qs
        return Response(_paginate(qs, request, AgentProductListSerializer))


class AgentProductDetailView(AgentEndpointView):
    """商品详情, 含当前库存."""

    def get(self, request, slug):
        product = get_object_or_404(
            Product.objects.select_related("category"), slug=slug, is_active=True
        )
        return Response(AgentProductDetailSerializer(product).data)


class AgentCategoryTreeView(AgentEndpointView):
    """分类树 (仅启用分类)."""

    def get(self, request):
        roots = Category.objects.filter(parent__isnull=True, is_active=True)
        return Response(AgentCategoryTreeSerializer(roots, many=True).data)


class AgentFeaturedProductListView(AgentEndpointView):
    """管理员标记的精选商品 (is_featured). 集合量小, 不分页."""

    def get(self, request):
        qs = Product.objects.filter(is_active=True, is_featured=True).select_related(
            "category"
        )
        return Response(AgentProductListSerializer(qs, many=True).data)


# ---------------------------------------------------------------------------
# 买家私有数据 (需 X-User-Id)
# ---------------------------------------------------------------------------


class AgentCartView(AgentEndpointView):
    """当前买家的购物车 (只读, 不建 cart 行)."""

    def get(self, request):
        buyer = _resolve_buyer(request)
        cart = Cart.objects.filter(user=buyer).first()
        return Response(build_cart_payload(cart))


class AgentOrderListView(AgentEndpointView):
    """当前买家的订单列表 (新单在前) 与下单 (POST).

    POST 挂在**这个类**上而不是另起一个, 是因为 URL 只有一个 `orders/`: Django 按
    路径先匹配, 同路径注册两个 view 只有第一个会命中. `get` 一行没动.
    """

    def get(self, request):
        buyer = _resolve_buyer(request)
        qs = Order.objects.filter(user=buyer).prefetch_related("items")
        return Response(_paginate(qs, request, AgentOrderListSerializer))

    def post(self, request):
        """下单: 把购物车**整个**下掉 (body 只有可选的 address_id).

        买家面 `POST /api/minimall/orders/` 要 `cart_item_ids` —— 模型得先查一遍
        购物车再挑出买哪几件, 那正是 PRD §4.3 说的「凑几次调用才拼齐一个动作」.
        这里一次调用下整车: 要买的就是车里那几件, 没有第二种可能 (要少买就先
        改数量或移除).
        """
        buyer = _resolve_buyer(request)
        data = self._validated_data(AgentOrderCreateSerializer)
        order = create_order(buyer, _all_cart_item_ids(buyer), _address_id(buyer, data))
        return Response(AgentOrderDetailSerializer(order).data)


class AgentOrderDetailView(AgentEndpointView):
    """当前买家的订单详情, 含明细与状态时间线."""

    def get(self, request, order_no):
        buyer = _resolve_buyer(request)
        order = get_object_or_404(
            Order.objects.prefetch_related("items"), order_no=order_no, user=buyer
        )
        return Response(AgentOrderDetailSerializer(order).data)


class AgentProfileView(AgentEndpointView):
    """当前买家的余额与基本信息."""

    def get(self, request):
        buyer = _resolve_buyer(request)
        return Response(AgentProfileSerializer(buyer).data)


class AgentAddressListView(AgentEndpointView):
    """当前买家的收货地址."""

    def get(self, request):
        buyer = _resolve_buyer(request)
        return Response(AgentAddressSerializer(buyer.addresses.all(), many=True).data)


# ---------------------------------------------------------------------------
# 写操作 (需 X-User-Id; 规则全在 services.py, 这里只做解析与翻译)
# ---------------------------------------------------------------------------
# 四个购物车写操作回的**都是动作之后的整车**: 模型一句就能念出来("购物车里现在
# 有两件, 一共 30 元"), 不用再补一次 GET. 用 slug 而不是 cart_item_id 定位同理 ——
# slug 在商品页, 搜索结果, 购物车返回体里到处都能看到, 而主键对模型是个没有
# 语义的数字, 模型只能靠"上一轮第几个"去猜.


def _cart_payload(buyer) -> dict:
    """当前买家的购物车 (没有车行就是空车 —— 只读端点也这么答)."""
    return build_cart_payload(Cart.objects.filter(user=buyer).first())


def _product_id(slug: str) -> int:
    """slug → 商品 id; 找不到 / 已下架就抛 (与 service 同一个异常类型).

    这层翻译不做任何放行判断: 商品还在不在卖, 库存够不够, 由 service 在锁内
    重新校验, 不信这里的结论.
    """
    product_id = (
        Product.objects.filter(slug=slug, is_active=True)
        .values_list("id", flat=True)
        .first()
    )
    if product_id is None:
        raise ProductUnavailableError(f"商品 {slug} 不存在或已下架")
    return product_id


def _cart_item_id_by_slug(buyer, slug: str) -> int:
    """slug → 购物车条目 id (**只在这个买家的车里找**).

    归属写进过滤条件: 别人的同款商品在这个条件下等同于不存在, 而 service 里
    还会再查一次 (两个请求同时动手时, 结论一律以锁内那次为准).
    """
    item_id = (
        CartItem.objects.filter(cart__user=buyer, product__slug=slug)
        .values_list("id", flat=True)
        .first()
    )
    if item_id is None:
        raise CartItemNotFoundError(f"购物车里的 {slug} 不存在")
    return item_id


def _all_cart_item_ids(buyer) -> list[int]:
    """车里现有的全部条目 id (下单下整车用)."""
    return list(CartItem.objects.filter(cart__user=buyer).values_list("id", flat=True))


def _address_id(buyer, data: dict) -> int:
    """要寄到哪儿: 传了地址就用传的, 没传就用默认地址.

    「没有默认地址」由端点判 —— 买家面那条路必传 address_id, 没有"默认"这个概念,
    所以这不是 service 的规则. 而模型替买家下单时最常踩的就是这个坑: 地址得先在
    页面上加一条.
    """
    address_id = data.get("address_id")
    if address_id is not None:
        return address_id
    default = ShippingAddress.objects.filter(user=buyer, is_default=True).first()
    if default is None:
        raise AgentRefusalError("no_default_address")
    return default.id


def _own_order(buyer, order_no: str) -> Order:
    """按订单号取**这个买家自己的**订单; 不是他的一律当没有.

    与只读的订单详情同一条规矩: 不存在与不属于你回同一个答案, 不区分原因 (防枚举).
    """
    order = Order.objects.filter(order_no=order_no, user=buyer).first()
    if order is None:
        raise AgentRefusalError("order_not_found")
    return order


class AgentCartItemAddView(AgentEndpointView):
    """加购 (POST cart/items/). 一次调用完成一个动作 —— 不用先查商品再建条目."""

    def post(self, request):
        buyer = _resolve_buyer(request)
        data = self._validated_data(AgentCartItemAddSerializer)
        add_to_cart(
            buyer, product_id=_product_id(data["slug"]), quantity=data["quantity"]
        )
        return Response(_cart_payload(buyer))


class AgentCartItemView(AgentEndpointView):
    """改数量 (PATCH) 与移除 (DELETE): `cart/items/<slug>/`, 都回动作之后的整车."""

    def patch(self, request, slug):
        buyer = _resolve_buyer(request)
        data = self._validated_data(AgentCartItemUpdateSerializer)
        update_cart_item(
            buyer,
            cart_item_id=_cart_item_id_by_slug(buyer, slug),
            quantity=data["quantity"],
        )
        return Response(_cart_payload(buyer))

    def delete(self, request, slug):
        buyer = _resolve_buyer(request)
        remove_cart_item(buyer, cart_item_id=_cart_item_id_by_slug(buyer, slug))
        return Response(_cart_payload(buyer))


class AgentCartClearView(AgentEndpointView):
    """清空购物车 (DELETE cart/clear/)."""

    def delete(self, request):
        buyer = _resolve_buyer(request)
        clear_cart(buyer)
        return Response(_cart_payload(buyer))


class AgentOrderCancelView(AgentEndpointView):
    """取消订单 (POST orders/<order_no>/cancel/).

    取消**即刻生效**: 不用审批 (要审批的是退款), 所以回执里直接给买家最关心的那件
    事 —— 回滚了几件库存. 只有未付款的订单走得通 (付款之后一律走退款), 所以回执里
    没有「退回余额」这回事.
    """

    def post(self, request, order_no):
        buyer = _resolve_buyer(request)
        return Response(
            AgentOrderCancelSerializer(cancel_order(_own_order(buyer, order_no))).data
        )


class AgentOrderPayView(AgentEndpointView):
    """付款 (POST orders/<order_no>/pay/) —— 用买家的支付密码付掉自己的一笔订单.

    形状与取消那条 (上面) 一样: 认人 → 取自己的单 → 调 service → 返回序列化结果.
    **不写 try/except** —— 密码错 / 余额不够 / 状态不允许都由 service 抛, 基类的
    `handle_exception` 按 `EXCEPTION_CODES` 统一翻 (那三条码早就在表里: 它们在
    issue 11 登记时就等着这个入口).

    密码从请求体来, 只活这一次请求: 它不落库 (没有哪一列装它)、不进日志 (Django
    的请求日志只记路径)、也不进响应. 要回到用户手里只有一条路 —— 他在页面上自己
    输的那一次 (ADR-0015).
    """

    def post(self, request, order_no):
        buyer = _resolve_buyer(request)
        data = self._validated_data(AgentOrderPaySerializer)
        # 用返回值渲染: pay_order 判的是锁内那份实例, 传进去的这份可能已过期
        paid = pay_order(_own_order(buyer, order_no), data["payment_password"])
        return Response(AgentOrderPaidSerializer(paid).data)


class AgentRefundView(AgentEndpointView):
    """退款: 申请 (POST refunds/) 与我的退款列表 (GET refunds/)."""

    def get(self, request):
        """我的退款列表 —— **不分页**.

        退款单天然少 (一笔订单同时最多一条进行中的), 故事 18 问「到哪一步了,
        为什么被驳回」列全更省事; 这是本组端点唯一一处偏离「列表都分页」的地方
        (issue 11 备注里写明了理由).
        """
        buyer = _resolve_buyer(request)
        refunds = RefundRequest.objects.filter(order__user=buyer).select_related(
            "order"
        )
        return Response(AgentRefundSerializer(refunds, many=True).data)

    def post(self, request):
        """申请退款: 给订单号就行, **不带金额** —— 金额由管理员批准时协商 (PRD §4.5).

        让模型有能力填金额等于把协商权交给模型, 与 PRD §4.2 相悖.
        """
        buyer = _resolve_buyer(request)
        data = self._validated_data(AgentRefundCreateSerializer)
        refund = request_refund(_own_order(buyer, data["order_no"]))
        return Response(AgentRefundSerializer(refund).data)
