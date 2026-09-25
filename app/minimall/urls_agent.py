from django.urls import path

from .views_agent import (
    AgentAddressListView,
    AgentCartClearView,
    AgentCartItemAddView,
    AgentCartItemView,
    AgentCartView,
    AgentCategoryTreeView,
    AgentFeaturedProductListView,
    AgentOrderCancelView,
    AgentOrderDetailView,
    AgentOrderListView,
    AgentOrderPayView,
    AgentProductDetailView,
    AgentProductListView,
    AgentProfileView,
    AgentRefundView,
)

app_name = "minimall_agent"

urlpatterns = [
    path("products/", AgentProductListView.as_view(), name="product_list"),
    path(
        "products/<slug:slug>/",
        AgentProductDetailView.as_view(),
        name="product_detail",
    ),
    path("categories/", AgentCategoryTreeView.as_view(), name="category_tree"),
    path(
        "featured-products/",
        AgentFeaturedProductListView.as_view(),
        name="featured_products",
    ),
    path("cart/", AgentCartView.as_view(), name="cart"),
    path("orders/", AgentOrderListView.as_view(), name="order_list"),
    path("orders/<str:order_no>/", AgentOrderDetailView.as_view(), name="order_detail"),
    path("profile/", AgentProfileView.as_view(), name="profile"),
    path("addresses/", AgentAddressListView.as_view(), name="address_list"),
    # --- 写端点 (issue 11) ---------------------------------------------
    # 一个路径一个 name: PATCH 与 DELETE 指同一条路由, 起两个 name 会留一个
    # 永远命不中的死 pattern (Django 按路径先匹配).
    path("cart/items/", AgentCartItemAddView.as_view(), name="cart_item_add"),
    path("cart/items/<slug:slug>/", AgentCartItemView.as_view(), name="cart_item"),
    path("cart/clear/", AgentCartClearView.as_view(), name="cart_clear"),
    path(
        "orders/<str:order_no>/cancel/",
        AgentOrderCancelView.as_view(),
        name="order_cancel",
    ),
    # 代付 (issue 35): 买家在自己页面上输的那次密码, 经恢复请求带到这里
    path(
        "orders/<str:order_no>/pay/",
        AgentOrderPayView.as_view(),
        name="order_pay",
    ),
    path("refunds/", AgentRefundView.as_view(), name="refunds"),
]
