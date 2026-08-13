"""Signal handlers — DB 变更后 (事务提交后) 失效缓存 (Cache-Aside)."""

import contextlib

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .cache import invalidate_category_cache, invalidate_product_cache
from .models import Category, Product, ProductImage


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
def clear_product_cache(sender, instance, **kwargs):
    """商品变更 — 事务提交后再删缓存, 防止回滚导致空缓存."""
    transaction.on_commit(lambda: invalidate_product_cache(instance))


@receiver(post_save, sender=ProductImage)
def clear_product_image_cache_on_save(sender, instance, **kwargs):
    """商品图片保存 — 失效缓存."""
    transaction.on_commit(lambda: invalidate_product_cache(instance.product))


@receiver(post_delete, sender=ProductImage)
def clear_product_image_cache_on_delete(sender, instance, **kwargs):
    """商品图片删除 — 失效缓存 + 清理磁盘文件."""
    transaction.on_commit(lambda: invalidate_product_cache(instance.product))
    if instance.image and instance.image.name:
        with contextlib.suppress(Exception):
            instance.image.storage.delete(instance.image.name)


@receiver(post_save, sender=Category)
@receiver(post_delete, sender=Category)
def clear_category_cache(sender, instance, **kwargs):
    """分类变更 — 事务提交后失效."""
    transaction.on_commit(invalidate_category_cache)
