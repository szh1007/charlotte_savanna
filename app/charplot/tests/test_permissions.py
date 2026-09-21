"""权限类单元测试.

IsStaff 行为锁定: 管理员放行 / 普通用户拒绝 / 匿名拒绝.
知识库管理等管理端接口直接复用.

IsInternalService 行为锁定: 正确令牌放行 / 错误拒绝 / 未配置拒绝 /
**非 ASCII 令牌拒绝而不是抛异常** (头值按 latin-1 解码, 见下方的用例说明).
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from app.charplot.permissions import IsInternalService, IsStaff

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


INTERNAL_TOKEN = "test-internal-token"


@override_settings(CHARPLOT_INTERNAL_TOKEN=INTERNAL_TOKEN)
class IsInternalServiceTests(TestCase):
    """内部令牌认证 — 四种输入, 四种结果."""

    def setUp(self):
        self.factory = RequestFactory()
        self.permission = IsInternalService()

    def _request_with(self, token):
        return self.factory.get(
            "/api/charplot/users/1/status-summary-input/",
            HTTP_X_INTERNAL_TOKEN=token,
        )

    def test_allows_matching_token(self):
        matched = self._request_with(INTERNAL_TOKEN)
        self.assertTrue(self.permission.has_permission(matched, None))

    def test_rejects_wrong_token(self):
        wrong = self._request_with("wrong-token")
        self.assertFalse(self.permission.has_permission(wrong, None))

    def test_rejects_non_ascii_token(self):
        """非 ASCII 令牌也是"错误令牌", 不能抛异常 (那会变成 500).

        请求头的值由 WSGI 按 latin-1 解码, 所以 `é` 正是"头里有一个 0xE9 字节"
        在服务端的样子; str 版 `compare_digest` 只接受 ASCII, 会当场抛 TypeError,
        于是"错误令牌 → 403"退化成"500 + 日志噪音".
        """
        non_ascii = self._request_with("tok" + chr(233))
        self.assertFalse(self.permission.has_permission(non_ascii, None))

    @override_settings(CHARPLOT_INTERNAL_TOKEN="")
    def test_rejects_everything_when_token_unconfigured(self):
        """环境变量没配置 → fail closed, 连"正确"的令牌也拒绝."""
        request = self._request_with(INTERNAL_TOKEN)
        self.assertFalse(self.permission.has_permission(request, None))
