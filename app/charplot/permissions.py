"""CharPlot 权限类 (Issue 02).

知识库管理等管理端接口 (Issue 09) 使用 IsStaff; 本 issue 以单元测试锁定行为.
"""

from rest_framework.permissions import BasePermission


class IsStaff(BasePermission):
    """仅管理员 (is_staff) 可访问."""

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_staff
        )
