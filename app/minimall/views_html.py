from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from .models import Category, Product


class IndexView(TemplateView):
    """首页 = 全部商品列表 + 搜索 + 筛选."""

    template_name = "minimall/index.html"

    def get_queryset(self, request):
        qs = (
            Product.objects.filter(is_active=True)
            .select_related("category")
            .prefetch_related("images")
        )
        search = request.GET.get("search", "").strip()
        if search:
            qs = qs.filter(name__icontains=search)
        category_slug = request.GET.get("category", "").strip()
        if category_slug == "_featured":
            qs = qs.filter(is_featured=True)
        elif category_slug:
            try:
                cat = Category.objects.get(slug=category_slug)
                qs = qs.filter(category__in=cat.get_descendants(include_self=True))
            except Category.DoesNotExist:
                qs = qs.none()
        min_price = request.GET.get("min_price")
        if min_price:
            qs = qs.filter(price__gte=min_price)
        max_price = request.GET.get("max_price")
        if max_price:
            qs = qs.filter(price__lte=max_price)
        ordering = request.GET.get("ordering")
        if ordering:
            qs = qs.order_by(ordering, "sort_order")
        else:
            qs = qs.order_by("sort_order", "-created_at")
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset(self.request)
        from django.core.paginator import Paginator

        page_size = int(self.request.GET.get("page_size", 5))
        if page_size not in (5, 10, 20, 50, 100):
            page_size = 5
        paginator = Paginator(qs, page_size)
        page_num = int(self.request.GET.get("page", 1))
        page = paginator.get_page(page_num)
        context["categories"] = Category.objects.filter(is_active=True).order_by(
            "sort_order", "name"
        )
        context["products"] = page.object_list
        context["total"] = paginator.count
        context["page"] = page.number
        context["total_pages"] = paginator.num_pages
        context["page_size"] = page_size
        context["featured_products"] = Product.objects.filter(
            is_active=True, is_featured=True
        ).prefetch_related("images")[:4]
        context["search"] = self.request.GET.get("search", "")
        context["selected_category"] = self.request.GET.get("category", "")
        context["min_price"] = self.request.GET.get("min_price", "")
        context["max_price"] = self.request.GET.get("max_price", "")
        context["ordering"] = self.request.GET.get("ordering", "-created_at")
        return context


class ProductDetailView(TemplateView):
    template_name = "minimall/product_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = get_object_or_404(
            Product.objects.filter(is_active=True)
            .select_related("category")
            .prefetch_related("images"),
            slug=kwargs["slug"],
        )
        context["product"] = product
        return context


class LoginPageView(TemplateView):
    template_name = "minimall/login.html"


class RegisterPageView(TemplateView):
    template_name = "minimall/register.html"


class CartPageView(LoginRequiredMixin, TemplateView):
    template_name = "minimall/cart.html"


class CheckoutPageView(LoginRequiredMixin, TemplateView):
    template_name = "minimall/checkout.html"


class OrderListPageView(LoginRequiredMixin, TemplateView):
    template_name = "minimall/order_list.html"


class OrderDetailPageView(LoginRequiredMixin, TemplateView):
    template_name = "minimall/order_detail.html"


class ProfilePageView(LoginRequiredMixin, TemplateView):
    template_name = "minimall/profile.html"


class AddressPageView(LoginRequiredMixin, TemplateView):
    template_name = "minimall/addresses.html"
