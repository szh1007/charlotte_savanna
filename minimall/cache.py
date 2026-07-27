"""缓存工具函数."""

from django.core.cache import cache

from .models import Category

CATEGORY_TREE_KEY = "minimall:category_tree"
PRODUCT_LIST_PREFIX = "minimall:product_list"
PRODUCT_DETAIL_PREFIX = "minimall:product_detail"
FEATURED_PRODUCTS_KEY = "minimall:featured_products"

PRODUCT_LIST_TTL = 300  # 5 min
PRODUCT_DETAIL_TTL = 600  # 10 min
FEATURED_TTL = 300  # 5 min


def get_cached_category_tree():
    """获取分类树, 优先读缓存."""
    data = cache.get(CATEGORY_TREE_KEY)
    if data is None:
        from .serializers import CategoryTreeSerializer

        roots = Category.objects.filter(parent__isnull=True, is_active=True)
        data = CategoryTreeSerializer(roots, many=True).data
        cache.set(CATEGORY_TREE_KEY, data, timeout=None)
    return data


def get_cached_product_list(params_hash, loader):
    """获取商品列表, 优先读缓存."""
    key = f"{PRODUCT_LIST_PREFIX}:{params_hash}"
    data = cache.get(key)
    if data is None:
        data = loader()
        cache.set(key, data, timeout=PRODUCT_LIST_TTL)
    return data


def get_cached_product_detail(slug, loader):
    """获取商品详情, 优先读缓存."""
    key = f"{PRODUCT_DETAIL_PREFIX}:{slug}"
    data = cache.get(key)
    if data is None:
        data = loader()
        cache.set(key, data, timeout=PRODUCT_DETAIL_TTL)
    return data


def invalidate_product_cache(product):
    """商品变更时失效对应缓存."""
    cache.delete(f"{PRODUCT_DETAIL_PREFIX}:{product.slug}")
    cache.delete_pattern(f"{PRODUCT_LIST_PREFIX}:*")
    cache.delete(FEATURED_PRODUCTS_KEY)


def invalidate_category_cache():
    """分类变更时失效分类树缓存和关联商品列表缓存."""
    cache.delete(CATEGORY_TREE_KEY)
    cache.delete_pattern(f"{PRODUCT_LIST_PREFIX}:*")
