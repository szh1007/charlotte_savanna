import django_filters
from django.db import models

from .models import Category, Product


class ProductFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search", label="Search")
    category = django_filters.CharFilter(method="filter_category", label="Category")
    min_price = django_filters.NumberFilter(
        field_name="price", lookup_expr="gte", label="Min price"
    )
    max_price = django_filters.NumberFilter(
        field_name="price", lookup_expr="lte", label="Max price"
    )
    ordering = django_filters.OrderingFilter(
        fields={
            "price": "price",
            "-price": "-price",
            "created_at": "created_at",
            "-created_at": "-created_at",
            "name": "name",
        },
        label="Ordering",
    )

    class Meta:
        model = Product
        fields = []

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            models.Q(name__icontains=value) | models.Q(description__icontains=value)
        )

    def filter_category(self, queryset, name, value):
        try:
            category = Category.objects.get(slug=value)
            descendants = category.get_descendants(include_self=True)
            return queryset.filter(category__in=descendants)
        except Category.DoesNotExist:
            return queryset.none()
