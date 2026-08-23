"""CharPlot 权限类.

账号体系 (Issue 02) 的 IsStaff 用于知识库管理等管理端接口 (Issue 09);
IsInternalService (Issue 03) 供 FastAPI 调 Django 内部端点使用.
"""

import secrets

from django.conf import settings
from rest_framework.permissions import BasePermission


class IsStaff(BasePermission):
    """仅管理员 (is_staff) 可访问."""

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_staff
        )


class IsInternalService(BasePermission):
    """仅 FastAPI 服务可调用 (Issue 03).

    校验请求头 X-Internal-Token == settings.CHARPLOT_INTERNAL_TOKEN.
    服务间写记录必经 Django API (DESIGN.md §2), 前端拿不到该 token;
    token 未配置时拒绝 (fail closed), 常量时间比较防时序侧信道.
    """

    def has_permission(self, request, view):
        token = request.META.get("HTTP_X_INTERNAL_TOKEN", "")
        expected = getattr(settings, "CHARPLOT_INTERNAL_TOKEN", "")
        if not expected:
            return False
        return secrets.compare_digest(token, expected)
