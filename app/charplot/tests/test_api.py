"""账号体系 API 测试 (Issue 02).

覆盖注册/登录/登出/会话/个人主页/连胜冻结/CSRF 全流程.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app.charplot.models import CharplotProfile, CharplotUserEvent
from app.charplot.services import FREEZE_DAYS

User = get_user_model()

REGISTER_URL = "/api/charplot/auth/register/"
LOGIN_URL = "/api/charplot/auth/login/"
LOGOUT_URL = "/api/charplot/auth/logout/"
SESSION_URL = "/api/charplot/auth/session/"
PROFILE_URL = "/api/charplot/profile/"
FREEZE_URL = "/api/charplot/profile/streak-freeze/"


class RegisterTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_register_creates_user_and_profile(self):
        resp = self.client.post(
            REGISTER_URL,
            {
                "username": "alice",
                "email": "alice@example.com",
                "password": "TestPass#2026",
            },
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["username"], "alice")
        # profile 自动创建 (Issue 01 骨架约定)
        user = User.objects.get(username="alice")
        self.assertTrue(CharplotProfile.objects.filter(user=user).exists())

    def test_register_duplicate_username_rejected(self):
        User.objects.create_user(
            username="bob", email="bob@example.com", password="TestPass#2026"
        )
        resp = self.client.post(
            REGISTER_URL,
            {
                "username": "bob",
                "email": "other@example.com",
                "password": "TestPass#2026",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("username", resp.json())

    def test_register_duplicate_email_rejected(self):
        User.objects.create_user(
            username="bob", email="bob@example.com", password="TestPass#2026"
        )
        resp = self.client.post(
            REGISTER_URL,
            {
                "username": "alice",
                "email": "bob@example.com",
                "password": "TestPass#2026",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("email", resp.json())

    def test_register_weak_password_rejected(self):
        resp = self.client.post(
            REGISTER_URL,
            {"username": "alice", "email": "alice@example.com", "password": "short"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("password", resp.json())

    def test_register_does_not_create_session(self):
        self.client.post(
            REGISTER_URL,
            {
                "username": "alice",
                "email": "alice@example.com",
                "password": "TestPass#2026",
            },
        )
        # 注册不自动登录, 未认证访问 profile 仍被拒
        resp = self.client.get(PROFILE_URL)
        self.assertEqual(resp.status_code, 403)


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )

    def test_login_success_records_login_event(self):
        resp = self.client.post(
            LOGIN_URL, {"username": "alice", "password": "TestPass#2026"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["username"], "alice")
        self.assertFalse(data["is_staff"])
        # 登录事件落库 (SPEC §8)
        today = timezone.localdate()
        self.assertTrue(
            CharplotUserEvent.objects.filter(
                user=self.user,
                event_type=CharplotUserEvent.EventType.LOGIN,
                event_date=today,
            ).exists()
        )

    def test_login_wrong_password_rejected(self):
        resp = self.client.post(
            LOGIN_URL, {"username": "alice", "password": "wrong-pass"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_login_inactive_user_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        resp = self.client.post(
            LOGIN_URL, {"username": "alice", "password": "TestPass#2026"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_login_creates_profile_for_legacy_user(self):
        # 手工建号 (无 profile) 的用户登录后自动兜底创建
        legacy = User.objects.create_user(
            username="legacy", email="legacy@example.com", password="TestPass#2026"
        )
        resp = self.client.post(
            LOGIN_URL, {"username": "legacy", "password": "TestPass#2026"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(CharplotProfile.objects.filter(user=legacy).exists())

    def test_logout_ends_session(self):
        self.client.post(LOGIN_URL, {"username": "alice", "password": "TestPass#2026"})
        resp = self.client.post(LOGOUT_URL)
        self.assertEqual(resp.status_code, 204)
        # 登出后 profile 不可访问
        self.assertEqual(self.client.get(PROFILE_URL).status_code, 403)

    def test_multiple_logins_same_day_count_once(self):
        self.client.post(LOGIN_URL, {"username": "alice", "password": "TestPass#2026"})
        self.client.post(LOGOUT_URL)
        self.client.post(LOGIN_URL, {"username": "alice", "password": "TestPass#2026"})
        count = (
            CharplotUserEvent.objects.filter(
                user=self.user, event_type=CharplotUserEvent.EventType.LOGIN
            )
            .values("event_date")
            .distinct()
            .count()
        )
        self.assertEqual(count, 1)


class SessionViewTests(TestCase):
    def test_session_returns_anonymous(self):
        resp = APIClient().get(SESSION_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"authenticated": False, "user": None})

    def test_session_returns_authenticated_user(self):
        client = APIClient()
        user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        client.force_login(user)
        resp = client.get(SESSION_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"]["username"], "alice")

    def test_session_sets_csrf_cookie_for_anonymous(self):
        resp = APIClient().get(SESSION_URL)
        self.assertIn("csrftoken", resp.cookies)


class ProfileTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        self.profile = CharplotProfile.objects.create(user=self.user)
        self.client.force_login(self.user)

    def test_profile_requires_auth(self):
        resp = APIClient().get(PROFILE_URL)
        self.assertEqual(resp.status_code, 403)

    def test_profile_fields_match_model(self):
        self.profile.xp = 120
        self.profile.level = 3
        self.profile.streak = 2
        self.profile.max_streak = 5
        self.profile.hearts = 3
        self.profile.coins = 42
        self.profile.save(
            update_fields=["xp", "level", "streak", "max_streak", "hearts", "coins"]
        )
        resp = self.client.get(PROFILE_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["username"], "alice")
        self.assertFalse(data["is_staff"])
        self.assertEqual(data["xp"], 120)
        self.assertEqual(data["level"], 3)
        self.assertEqual(data["streak"], 2)
        self.assertEqual(data["max_streak"], 5)
        self.assertEqual(data["hearts"], 3)
        self.assertEqual(data["coins"], 42)
        self.assertIn("stats", data)
        self.assertIn("login_days", data["stats"])
        self.assertIn("streak_loss_warning", data)

    def test_profile_login_days_from_events(self):
        today = timezone.localdate()
        CharplotUserEvent.objects.create(
            user=self.user,
            event_type=CharplotUserEvent.EventType.LOGIN,
            event_date=today - timedelta(days=2),
        )
        CharplotUserEvent.objects.create(
            user=self.user,
            event_type=CharplotUserEvent.EventType.LOGIN,
            event_date=today - timedelta(days=1),
        )
        CharplotUserEvent.objects.create(
            user=self.user,
            event_type=CharplotUserEvent.EventType.LOGIN,
            event_date=today,
        )
        data = self.client.get(PROFILE_URL).json()
        self.assertEqual(data["stats"]["login_days"], 3)

    def test_profile_is_staff_flag(self):
        staff = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="TestPass#2026",
            is_staff=True,
        )
        CharplotProfile.objects.create(user=staff)
        client = APIClient()
        client.force_login(staff)
        data = client.get(PROFILE_URL).json()
        self.assertTrue(data["is_staff"])


class StreakFreezeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        self.profile = CharplotProfile.objects.create(user=self.user, coins=30)
        self.client.force_login(self.user)

    def test_freeze_deducts_coins_and_sets_until(self):
        today = timezone.localdate()
        resp = self.client.post(FREEZE_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["coins"], 20)
        # JSON 序列化后日期为 ISO 字符串
        self.assertEqual(data["frozen"], str(today + timedelta(days=FREEZE_DAYS)))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.coins, 20)
        self.assertEqual(self.profile.freeze_until, today + timedelta(days=FREEZE_DAYS))

    def test_freeze_stacks_when_not_expired(self):
        today = timezone.localdate()
        self.client.post(FREEZE_URL)
        self.client.post(FREEZE_URL)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.coins, 10)
        self.assertEqual(
            self.profile.freeze_until, today + timedelta(days=FREEZE_DAYS * 2)
        )

    def test_freeze_rejected_with_zero_coins(self):
        self.profile.coins = 0
        self.profile.save(update_fields=["coins"])
        resp = self.client.post(FREEZE_URL)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("detail", resp.json())

    def test_freeze_requires_auth(self):
        resp = APIClient().post(FREEZE_URL)
        self.assertEqual(resp.status_code, 403)


class CsrfFlowTests(TestCase):
    """CSRF 全流程: GET session 拿 cookie → 带 token 登录成功; 无 token 被拒."""

    def setUp(self):
        User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        # 启用 CSRF 校验的客户端
        self.client = APIClient(enforce_csrf_checks=True)

    def _token_from_cookie(self, resp):
        return resp.cookies["csrftoken"].value

    def test_login_without_csrf_token_rejected(self):
        resp = self.client.post(
            LOGIN_URL, {"username": "alice", "password": "TestPass#2026"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_login_with_csrf_token_succeeds(self):
        # 前置 GET 建立 csrftoken cookie (SPA 启动路径)
        resp = self.client.get(SESSION_URL)
        self.assertEqual(resp.status_code, 200)
        token = self._token_from_cookie(resp)
        resp = self.client.post(
            LOGIN_URL,
            {"username": "alice", "password": "TestPass#2026"},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 200)
