"""权限类单元测试 (Issue 02).

IsStaff 行为锁定: 管理员放行 / 普通用户拒绝 / 匿名拒绝.
知识库管理等管理端接口 (Issue 09) 直接复用.
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from app.charplot.permissions import IsStaff

User = get_user_model()


class IsStaffTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.permission = IsStaff()

    def _request_by(self, user):
        request = self.factory.get("/api/charplot/profile/")
        request.user = user
        return request

    def test_allows_staff(self):
        staff = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="TestPass#2026",
            is_staff=True,
        )
        self.assertTrue(self.permission.has_permission(self._request_by(staff), None))

    def test_rejects_regular_user(self):
        user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        self.assertFalse(self.permission.has_permission(self._request_by(user), None))

    def test_rejects_anonymous(self):
        request = self.factory.get("/api/charplot/profile/")
        request.user = None
        self.assertFalse(self.permission.has_permission(request, None))
