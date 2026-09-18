"""Agent 内部端点 (CharApp 助手专用, 全部只读).

前缀 /api/minimall/agent/, 认证 = X-Internal-Token (未配置即全拒, fail closed);
需要买家身份的端点再带 X-User-Id, 身份由调用方声明 (信任模型与生产化路径
见 CharApp/PRD.md §4.10).

商品数据一律直查数据库, 不走 Redis 缓存: 缓存里的库存可能已经过期十分钟,
而助手报出的"还剩 3 件"会被买家当作事实.
"""

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .filters import ProductFilter
from .models import Cart, Category, Order, Product
from .permissions import IsInternalService
from .serializers_agent import (
    AgentAddressSerializer,
    AgentCategoryTreeSerializer,
    AgentOrderDetailSerializer,
    AgentOrderListSerializer,
    AgentProductDetailSerializer,
    AgentProductListSerializer,
    AgentProfileSerializer,
    build_cart_payload,
)

User = get_user_model()

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _paginate(queryset, request, serializer_class) -> dict:
    """统一分页返回体; 页码/页大小非法时回退默认值."""
    try:
        page_size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.query_params.get("page", 1))
    return {
        "count": paginator.count,
        "page": page.number,
        "page_size": page_size,
        "total_pages": paginator.num_pages,
        "results": serializer_class(page.object_list, many=True).data,
    }


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
    """

    authentication_classes = []
    permission_classes = [IsInternalService]


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
    """当前买家的订单列表 (新单在前)."""

    def get(self, request):
        buyer = _resolve_buyer(request)
        qs = Order.objects.filter(user=buyer).prefetch_related("items")
        return Response(_paginate(qs, request, AgentOrderListSerializer))


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
