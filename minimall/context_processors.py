"""Admin context processor — 注入 Redis 健康状态到管理后台."""

from django.core.cache import cache


def redis_status(request):
    """在每个 admin 请求中检查 Redis, 返回状态供模板使用."""
    if not request.path.startswith("/admin/"):
        return {}
    try:
        cache.get("_admin_health_check")
        return {"redis_status": True}
    except Exception:
        return {"redis_status": False}
