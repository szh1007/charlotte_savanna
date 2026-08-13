"""Custom DRF permissions."""

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
