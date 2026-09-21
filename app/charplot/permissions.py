"""CharPlot 权限类.

账号体系的 IsStaff 用于知识库管理等管理端接口;
IsInternalService 供 FastAPI 调 Django 内部端点使用.
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
    """仅 FastAPI 服务可调用.

    校验请求头 X-Internal-Token == settings.CHARPLOT_INTERNAL_TOKEN.
    服务间写记录必经 Django API, 前端拿不到该 token;
    token 未配置时拒绝 (fail closed), 常量时间比较防时序侧信道.
    """

    def has_permission(self, request, view):
        expected = getattr(settings, "CHARPLOT_INTERNAL_TOKEN", "")
        if not expected:
            return False
        # 头值由 WSGI 按 latin-1 解码, 可能含非 ASCII 字符; str 版 compare_digest
        # 只接受 ASCII, 碰到这类请求会抛 TypeError (500). 转 bytes 再比, 既恒定
        # 时间又不会因请求内容崩溃.
        token = request.META.get("HTTP_X_INTERNAL_TOKEN", "")
        return secrets.compare_digest(
            token.encode("utf-8", "surrogateescape"), expected.encode("utf-8")
        )
