"""CharPlot API 视图 (DRF)."""

import logging

from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthView(APIView):
    """健康检查 - 探活 MySQL 与 Redis.

    三端联通的基础链路 (Issue 01): 前端 / 运维可轮询此端点确认 Django 侧就绪.
    """

    # 健康检查无需认证
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        # MySQL: ensure_connection 失败会抛异常, 捕获后标记 db 不健康
        db_status = "ok"
        try:
            connection.ensure_connection()
        except Exception as exc:
            db_status = "error"
            logger.error("CharPlot health: DB check failed: %s", exc)

        # Redis: django-redis 客户端提供 ping()
        redis_status = "ok"
        try:
            from django.core.cache import cache

            cache.client.get_client().ping()
        except Exception as exc:
            redis_status = "error"
            logger.error("CharPlot health: Redis check failed: %s", exc)

        is_ok = db_status == "ok" and redis_status == "ok"
        payload = {
            "status": "ok" if is_ok else "degraded",
            "service": "charplot-django",
            "db": db_status,
            "redis": redis_status,
            "time": timezone.now().isoformat(),
        }
        code = status.HTTP_200_OK if is_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=code)
