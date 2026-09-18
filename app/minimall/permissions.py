"""Custom DRF permissions."""

import secrets

from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsOwnerOrAdmin(BasePermission):
    """Owner or admin access.

    - Admin (is_staff) → full access.
    - Object owner (obj.user == request.user) → access.
    - Otherwise → denied.
    """

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        if hasattr(obj, "user"):
            return obj.user == request.user
        return False


class IsAdminOrReadOnly(BasePermission):
    """Admin write, anyone read.

    - Safe methods (GET, HEAD, OPTIONS) → anyone.
    - Write methods → is_staff only.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_authenticated and request.user.is_staff


class IsInternalService(BasePermission):
    """仅 CharApp 助手服务可调用 (agent 内部端点).

    校验请求头 X-Internal-Token == settings.CHARAPP_INTERNAL_TOKEN,
    与 app/charplot/permissions.py 的同类实现保持同一写法.
    令牌未配置时拒绝 (fail closed), 常量时间比较防时序侧信道.
    """

    def has_permission(self, request, view):
        expected = getattr(settings, "CHARAPP_INTERNAL_TOKEN", "")
        if not expected:
            return False
        # 头值由 WSGI 按 latin-1 解码, 可能含非 ASCII 字符; str 版 compare_digest
        # 只接受 ASCII, 碰到这类请求会抛 TypeError (500). 转 bytes 再比, 既恒定
        # 时间又不会因请求内容崩溃.
        token = request.META.get("HTTP_X_INTERNAL_TOKEN", "")
        return secrets.compare_digest(
            token.encode("utf-8", "surrogateescape"), expected.encode("utf-8")
        )
