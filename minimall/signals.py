"""Signal handlers — 数据变更时自动失效缓存."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .cache import invalidate_category_cache, invalidate_product_cache
from .models import Category, Product, ProductImage


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
def clear_product_cache(sender, instance, **kwargs):
    invalidate_product_cache(instance)


@receiver(post_save, sender=ProductImage)
@receiver(post_delete, sender=ProductImage)
def clear_product_image_cache(sender, instance, **kwargs):
    invalidate_product_cache(instance.product)


@receiver(post_save, sender=Category)
@receiver(post_delete, sender=Category)
def clear_category_cache(sender, instance, **kwargs):
    invalidate_category_cache()
