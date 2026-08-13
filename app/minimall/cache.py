"""缓存工具函数 — 防击穿 / 防穿透 / 防雪崩 / 防自旋 / 防全量扫描 / 防宕机 / 预热."""

import contextlib
import logging
import random
import time
from functools import wraps

from django.core.cache import cache

from .models import Category

logger = logging.getLogger(__name__)

CATEGORY_TREE_KEY = "minimall:category_tree"
PRODUCT_LIST_PREFIX = "minimall:product_list"
PRODUCT_DETAIL_PREFIX = "minimall:product_detail"
FEATURED_PRODUCTS_KEY = "minimall:featured_products"

PRODUCT_LIST_INDEX_KEY = "minimall:product_list:index"

PRODUCT_LIST_TTL = 300
PRODUCT_DETAIL_TTL = 600
FEATURED_TTL = 300

NULL_CACHE_TTL = 60
CATEGORY_TREE_MAX_TTL = 3600
LOCK_TIMEOUT = 60  # 足够覆盖任何正常 DB 查询
PUBSUB_WAIT_TIMEOUT = 3  # Pub/Sub 等待最大秒数, 超时走兜底
TTL_JITTER_RANGE = 0.2

CACHE_READY_CHANNEL = "minimall:cache_ready"

# Redis 健康状态 — 连续失败 N 次后熔断, 直接走 DB
_failure_count = 0
FAILURE_THRESHOLD = 3
CIRCUIT_BREAKER_TTL = 30  # 熔断后 30 秒尝试恢复


# ---------------------------------------------------------------------------
# 熔断器 + 安全调用
# ---------------------------------------------------------------------------


def _redis_healthy() -> bool:
    """检查 Redis 是否可用 (有熔断保护)."""
    global _failure_count
    return _failure_count < FAILURE_THRESHOLD


def _mark_success() -> None:
    global _failure_count
    if _failure_count > 0:
        logger.info("Redis recovered after %d failure(s)", _failure_count)
    _failure_count = 0


def _mark_failure(op_name: str = "", error: str = "") -> None:
    global _failure_count
    _failure_count += 1
    logger.error(
        "Redis operation FAILED [%s] (attempt %d/%d): %s",
        op_name,
        _failure_count,
        FAILURE_THRESHOLD,
        error,
    )
    if _failure_count >= FAILURE_THRESHOLD:
        logger.warning(
            "Redis circuit breaker OPEN — all cache ops will bypass for %ss",
            CIRCUIT_BREAKER_TTL,
        )
        import threading

        threading.Timer(CIRCUIT_BREAKER_TTL, _reset_circuit).start()


def _reset_circuit() -> None:
    global _failure_count
    _failure_count = 0
    logger.info("Redis circuit breaker CLOSED — cache operations resumed")


def _safe_cache(op, op_name: str = "cache", default_return=None):
    """安全执行缓存操作. Redis 不可用时返回 default_return, 记录日志, 不抛异常."""

    @wraps(op)
    def wrapper(*args, **kw):
        if not _redis_healthy():
            logger.debug("Redis bypassed (circuit open) — %s", op_name)
            return default_return
        try:
            result = op(*args, **kw)
            _mark_success()
            return result
        except Exception as e:
            _mark_failure(op_name, str(e))
            return default_return

    return wrapper


# ---------------------------------------------------------------------------
# 包装后的安全缓存操作
# ---------------------------------------------------------------------------


def _cache_get(key):
    return _safe_cache(cache.get, "get", default_return=None)(key)


def _cache_set(key, value, timeout=None):
    return _safe_cache(cache.set, "set", default_return=False)(key, value, timeout)


def _cache_add(key, value, timeout=None):
    return _safe_cache(cache.add, "add", default_return=False)(key, value, timeout)


def _cache_delete(key):
    return _safe_cache(cache.delete, "delete", default_return=False)(key)


def _cache_delete_pattern(pattern):
    return _safe_cache(cache.delete_pattern, "delete_pattern", default_return=False)(
        pattern
    )


def _cache_client():
    """安全获取 Redis 原生 client. 不可用时返回 None."""
    if not _redis_healthy():
        return None
    try:
        return cache.client
    except Exception:
        _mark_failure()
        return None


# ---------------------------------------------------------------------------
# 帮助函数
# ---------------------------------------------------------------------------


def _jitter(base_ttl: int) -> int:
    delta = int(base_ttl * TTL_JITTER_RANGE)
    return base_ttl + random.randint(-delta, delta)


def _index_key(product_list_key: str) -> None:
    client = _cache_client()
    if client is None:
        return
    try:
        client.sadd(PRODUCT_LIST_INDEX_KEY, product_list_key)
        # 给索引 Set 设 TTL 防泄漏 (每次 SADD 续期, 24h 无写入自动清理)
        client.expire(PRODUCT_LIST_INDEX_KEY, 86400)
        _mark_success()
    except Exception:
        _mark_failure()


def _delete_all_product_list() -> None:
    client = _cache_client()
    if client is None:
        return
    try:
        keys = client.smembers(PRODUCT_LIST_INDEX_KEY)
        if keys:
            pipeline = client.pipeline()
            for k in keys:
                pipeline.delete(k)
            pipeline.delete(PRODUCT_LIST_INDEX_KEY)
            pipeline.execute()
        _mark_success()
    except Exception:
        _mark_failure()
        # 兜底
        _cache_delete_pattern(f"{PRODUCT_LIST_PREFIX}:*")


def _lock_and_load(key: str, loader, ttl: int):
    """带分布式锁的缓存加载. Redis 宕机时直接查 DB."""
    lock_key = f"{key}:lock"
    channel = f"{CACHE_READY_CHANNEL}:{key}"

    if not _redis_healthy():
        return loader()

    # 尝试获取锁
    acquired = _cache_add(lock_key, "1", timeout=LOCK_TIMEOUT)
    if acquired:
        try:
            data = loader()
            _cache_set(key, data, timeout=_jitter(ttl))
            # 通知等待者
            client = _cache_client()
            if client:
                with contextlib.suppress(Exception):
                    client.publish(channel, "ok")
            return data
        except Exception:
            _cache_delete(lock_key)
            raise
        finally:
            _cache_delete(lock_key)

    # 未获锁 — Pub/Sub 阻塞等待 + 轮询兜底 (防止 Pub/Sub 消息丢失)
    client = _cache_client()
    notified = False
    if client:
        with contextlib.suppress(Exception):
            pubsub = client.pubsub()
            pubsub.subscribe(channel)
            deadline = time.time() + PUBSUB_WAIT_TIMEOUT
            while time.time() < deadline:
                msg = pubsub.get_message(timeout=0.5)
                if msg and msg["type"] == "message":
                    notified = True
                    break
                data = _cache_get(key)
                if data is not None:
                    pubsub.close()
                    return data
            pubsub.close()

    # 缓存命中 (被通知或轮询检测到)
    if notified:
        data = _cache_get(key)
        if data is not None:
            return data

    # 超时兜底: 直接查 DB
    data = loader()
    _cache_set(key, data, timeout=_jitter(ttl))
    return data


def _get_or_load(key: str, loader, ttl: int, allow_null: bool = False):
    """通用缓存读取."""
    data = _cache_get(key)
    if data is not None:
        return data

    null_key = f"{key}:null"
    if allow_null and _cache_get(null_key):
        return None

    result = _lock_and_load(key, loader, ttl)

    if allow_null and result is None:
        _cache_set(null_key, "1", timeout=NULL_CACHE_TTL)

    return result


# ---------------------------------------------------------------------------
# 预热 — 启动时加载热门数据到缓存
# ---------------------------------------------------------------------------

_warmed_up = False


def warmup_cache():
    """预热缓存: 加载分类树 + 默认商品列表到 Redis.

    在 AppConfig.ready() 中调用, 避免冷启动穿透.
    """
    global _warmed_up
    if _warmed_up:
        return
    _warmed_up = True

    logger.info("Cache warmup started...")
    try:
        # 预热分类树
        from .serializers import CategoryTreeSerializer

        roots = Category.objects.filter(parent__isnull=True, is_active=True)
        data = CategoryTreeSerializer(roots, many=True).data
        _cache_set(CATEGORY_TREE_KEY, data, timeout=CATEGORY_TREE_MAX_TTL)
        logger.info("Category tree warmed up (%d roots)", len(data))

        # 预热默认商品列表 (首页, 无筛选)
        from .serializers import ProductListSerializer
        from .views_buyer import ProductListView

        qs = ProductListView.get_base_queryset()
        product_data = ProductListSerializer(qs[:20], many=True).data
        key = f"{PRODUCT_LIST_PREFIX}:default"
        _cache_set(key, product_data, timeout=_jitter(PRODUCT_LIST_TTL))
        _index_key(key)
        logger.info("Default product list warmed up (%d items)", len(product_data))
    except Exception as e:
        logger.warning("Cache warmup failed: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_cached_category_tree():
    data = _cache_get(CATEGORY_TREE_KEY)
    if data is None:
        from .serializers import CategoryTreeSerializer

        roots = Category.objects.filter(parent__isnull=True, is_active=True)
        data = CategoryTreeSerializer(roots, many=True).data
        _cache_set(CATEGORY_TREE_KEY, data, timeout=CATEGORY_TREE_MAX_TTL)
    return data


def get_cached_product_list(params_hash, loader):
    key = f"{PRODUCT_LIST_PREFIX}:{params_hash}"
    _index_key(key)
    return _get_or_load(key, loader, ttl=PRODUCT_LIST_TTL)


def get_cached_product_detail(slug, loader):
    key = f"{PRODUCT_DETAIL_PREFIX}:{slug}"
    return _get_or_load(key, loader, ttl=PRODUCT_DETAIL_TTL, allow_null=True)


def invalidate_product_cache(product):
    _cache_delete(f"{PRODUCT_DETAIL_PREFIX}:{product.slug}")
    _cache_delete(f"{PRODUCT_DETAIL_PREFIX}:{product.slug}:null")
    _delete_all_product_list()
    _cache_delete(FEATURED_PRODUCTS_KEY)


def invalidate_category_cache():
    _cache_delete(CATEGORY_TREE_KEY)
    _delete_all_product_list()
