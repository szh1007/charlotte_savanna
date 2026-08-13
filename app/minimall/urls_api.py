from django.urls import path

from .views_buyer import (
    AddCartItemView,
    AddressDetailView,
    AddressListView,
    CartView,
    CategoryTreeView,
    ChangePasswordView,
    ChangePaymentPasswordView,
    ClearCartView,
    DeleteCartItemView,
    LoginView,
    LogoutView,
    MeView,
    OrderActiveCountView,
    OrderCancelView,
    OrderCompleteView,
    OrderDetailView,
    OrderListView,
    OrderPayView,
    OrderReceiveView,
    ProductDetailView,
    ProductListView,
    RechargeView,
    RegisterView,
    UpdateCartItemView,
)

app_name = "minimall_api"

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="change_password"),
    path(
        "auth/change-payment-password/",
        ChangePaymentPasswordView.as_view(),
        name="change_payment_password",
    ),
    path("auth/recharge/", RechargeView.as_view(), name="recharge"),
    path("categories/", CategoryTreeView.as_view(), name="category_tree"),
    path("products/", ProductListView.as_view(), name="product_list"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="product_detail"),
    path("cart/", CartView.as_view(), name="cart"),
    path("cart/items/", AddCartItemView.as_view(), name="cart_add"),
    path(
        "cart/items/<int:cart_item_id>/",
        UpdateCartItemView.as_view(),
        name="cart_update",
    ),
    path(
        "cart/items/<int:cart_item_id>/delete/",
        DeleteCartItemView.as_view(),
        name="cart_delete",
    ),
    path("cart/clear/", ClearCartView.as_view(), name="cart_clear"),
    path("addresses/", AddressListView.as_view(), name="address_list"),
    path(
        "addresses/<int:address_id>/",
        AddressDetailView.as_view(),
        name="address_detail",
    ),
    path("orders/", OrderListView.as_view(), name="order_list"),
    path(
        "orders/active-count/",
        OrderActiveCountView.as_view(),
        name="order_active_count",
    ),
    path("orders/<str:order_no>/", OrderDetailView.as_view(), name="order_detail"),
    path("orders/<str:order_no>/pay/", OrderPayView.as_view(), name="order_pay"),
    path(
        "orders/<str:order_no>/cancel/", OrderCancelView.as_view(), name="order_cancel"
    ),
    path(
        "orders/<str:order_no>/receive/",
        OrderReceiveView.as_view(),
        name="order_receive",
    ),
    path(
        "orders/<str:order_no>/complete/",
        OrderCompleteView.as_view(),
        name="order_complete",
    ),
]
