from django.urls import path

from .views_html import (
    AddressPageView,
    CartPageView,
    CheckoutPageView,
    IndexView,
    LoginPageView,
    OrderDetailPageView,
    OrderListPageView,
    ProductDetailView,
    ProfilePageView,
    RegisterPageView,
)

app_name = "minimall_html"

urlpatterns = [
    path("", IndexView.as_view(), name="index"),
    path("login/", LoginPageView.as_view(), name="login"),
    path("register/", RegisterPageView.as_view(), name="register"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="product_detail"),
    path("cart/", CartPageView.as_view(), name="cart"),
    path("checkout/", CheckoutPageView.as_view(), name="checkout"),
    path("orders/", OrderListPageView.as_view(), name="order_list"),
    path("orders/<str:order_no>/", OrderDetailPageView.as_view(), name="order_detail"),
    path("profile/", ProfilePageView.as_view(), name="profile"),
    path("addresses/", AddressPageView.as_view(), name="addresses"),
]
