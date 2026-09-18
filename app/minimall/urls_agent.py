from django.urls import path

from .views_agent import (
    AgentAddressListView,
    AgentCartView,
    AgentCategoryTreeView,
    AgentFeaturedProductListView,
    AgentOrderDetailView,
    AgentOrderListView,
    AgentProductDetailView,
    AgentProductListView,
    AgentProfileView,
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
]
