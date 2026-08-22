"""服务层单元测试 (Issue 02).

连胜冻结 / 中断警告 / 事件记录 / 统计, 均通过 today 参数注入日期, 不 mock 时钟.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.charplot.models import CharplotProfile, CharplotUserEvent
from app.charplot.services import (
    FREEZE_COIN_COST,
    FREEZE_DAYS,
    InsufficientCoinsError,
    buy_streak_freeze,
    count_login_days,
    get_streak_loss_warning,
    record_event,
    settle_streak_on_login,
)

User = get_user_model()

TODAY = timezone.localdate()


class StreakLossWarningTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        self.profile = CharplotProfile.objects.create(user=user)

    def test_no_warning_when_never_studied(self):
        result = get_streak_loss_warning(self.profile, today=TODAY)
        self.assertFalse(result["warning"])

    def test_no_warning_when_studied_yesterday(self):
        self.profile.last_study_date = TODAY - timedelta(days=1)
        result = get_streak_loss_warning(self.profile, today=TODAY)
        self.assertFalse(result["warning"])
        self.assertEqual(result["missed_days"], 0)

    def test_no_warning_when_studied_today(self):
        self.profile.last_study_date = TODAY
        result = get_streak_loss_warning(self.profile, today=TODAY)
        self.assertFalse(result["warning"])

    def test_warning_after_two_full_missed_days(self):
        self.profile.last_study_date = TODAY - timedelta(days=3)
        result = get_streak_loss_warning(self.profile, today=TODAY)
        self.assertTrue(result["warning"])
        self.assertEqual(result["missed_days"], 2)

    def test_freeze_exempts_warning(self):
        self.profile.last_study_date = TODAY - timedelta(days=5)
        self.profile.freeze_until = TODAY  # 冻结期内 (含当日)
        result = get_streak_loss_warning(self.profile, today=TODAY)
        self.assertFalse(result["warning"])

    def test_expired_freeze_shows_warning(self):
        self.profile.last_study_date = TODAY - timedelta(days=5)
        self.profile.freeze_until = TODAY - timedelta(days=1)  # 冻结已过期
        result = get_streak_loss_warning(self.profile, today=TODAY)
        self.assertTrue(result["warning"])


class StreakSettleOnLoginTests(TestCase):
    """登录时惰性归零判定 (Issue 02 补充)."""

    def setUp(self):
        user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        self.profile = CharplotProfile.objects.create(user=user, streak=5)

    def test_skip_when_never_studied(self):
        settle_streak_on_login(self.profile, today=TODAY)
        self.assertEqual(self.profile.streak, 5)

    def test_skip_when_studied_today(self):
        # 今天已学习, 学习结算已处理 (last_study_date 由 Issue 05 更新)
        self.profile.last_study_date = TODAY
        self.profile.save(update_fields=["last_study_date"])
        settle_streak_on_login(self.profile, today=TODAY)
        self.assertEqual(self.profile.streak, 5)

    def test_keeps_streak_when_studied_yesterday(self):
        self.profile.last_study_date = TODAY - timedelta(days=1)
        self.profile.save(update_fields=["last_study_date"])
        settle_streak_on_login(self.profile, today=TODAY)
        self.assertEqual(self.profile.streak, 5)

    def test_resets_after_missed_days(self):
        self.profile.last_study_date = TODAY - timedelta(days=3)
        self.profile.save(update_fields=["last_study_date"])
        settle_streak_on_login(self.profile, today=TODAY)
        self.assertEqual(self.profile.streak, 0)

    def test_freeze_exempts_reset(self):
        self.profile.last_study_date = TODAY - timedelta(days=3)
        self.profile.freeze_until = TODAY  # 冻结保护期 (含当日)
        self.profile.save(update_fields=["last_study_date", "freeze_until"])
        settle_streak_on_login(self.profile, today=TODAY)
        self.assertEqual(self.profile.streak, 5)

    def test_expired_freeze_resets(self):
        self.profile.last_study_date = TODAY - timedelta(days=3)
        self.profile.freeze_until = TODAY - timedelta(days=1)  # 冻结已过期
        self.profile.save(update_fields=["last_study_date", "freeze_until"])
        settle_streak_on_login(self.profile, today=TODAY)
        self.assertEqual(self.profile.streak, 0)

    def test_idempotent_when_already_zero(self):
        self.profile.last_study_date = TODAY - timedelta(days=3)
        self.profile.streak = 0
        self.profile.save(update_fields=["last_study_date", "streak"])
        settle_streak_on_login(self.profile, today=TODAY)
        # 已归零不再写库; 重复登录重复执行无副作用
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.streak, 0)


class StreakFreezeServiceTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        self.profile = CharplotProfile.objects.create(user=user, coins=30)

    def test_freeze_deducts_configured_cost(self):
        buy_streak_freeze(self.profile, today=TODAY)
        self.assertEqual(self.profile.coins, 30 - FREEZE_COIN_COST)
        self.assertEqual(self.profile.freeze_until, TODAY + timedelta(days=FREEZE_DAYS))

    def test_freeze_stacks_from_existing_until(self):
        buy_streak_freeze(self.profile, today=TODAY)
        buy_streak_freeze(self.profile, today=TODAY + timedelta(days=1))
        self.assertEqual(
            self.profile.freeze_until, TODAY + timedelta(days=FREEZE_DAYS * 2)
        )

    def test_freeze_restarts_when_expired(self):
        self.profile.freeze_until = TODAY - timedelta(days=3)
        self.profile.save(update_fields=["freeze_until"])
        buy_streak_freeze(self.profile, today=TODAY)
        # 冻结已过期, 从今天重新起算, 不叠加历史
        self.assertEqual(self.profile.freeze_until, TODAY + timedelta(days=FREEZE_DAYS))

    def test_freeze_raises_when_coins_insufficient(self):
        self.profile.coins = FREEZE_COIN_COST - 1
        self.profile.save(update_fields=["coins"])
        with self.assertRaises(InsufficientCoinsError):
            buy_streak_freeze(self.profile, today=TODAY)
        # 失败不扣币
        self.assertEqual(self.profile.coins, FREEZE_COIN_COST - 1)
        self.assertIsNone(self.profile.freeze_until)


class RecordEventTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )

    def test_record_event_dedupes_same_day(self):
        record_event(self.user, CharplotUserEvent.EventType.LOGIN, event_date=TODAY)
        record_event(self.user, CharplotUserEvent.EventType.LOGIN, event_date=TODAY)
        count = CharplotUserEvent.objects.filter(
            user=self.user, event_type=CharplotUserEvent.EventType.LOGIN
        ).count()
        self.assertEqual(count, 1)

    def test_record_event_separate_days(self):
        record_event(self.user, CharplotUserEvent.EventType.LOGIN, event_date=TODAY)
        record_event(
            self.user,
            CharplotUserEvent.EventType.LOGIN,
            event_date=TODAY - timedelta(days=1),
        )
        count = CharplotUserEvent.objects.filter(
            user=self.user, event_type=CharplotUserEvent.EventType.LOGIN
        ).count()
        self.assertEqual(count, 2)

    def test_record_event_keeps_payload(self):
        event = record_event(
            self.user,
            CharplotUserEvent.EventType.ANSWER,
            event_date=TODAY,
            payload={"level_id": 3},
        )
        self.assertEqual(event.payload, {"level_id": 3})


class CountLoginDaysTests(TestCase):
    def test_counts_distinct_dates(self):
        user = User.objects.create_user(
            username="alice", email="alice@example.com", password="TestPass#2026"
        )
        for i in range(3):
            record_event(
                user,
                CharplotUserEvent.EventType.LOGIN,
                event_date=TODAY - timedelta(days=i),
            )
        # 同日两条重复记录只算 1 天
        record_event(user, CharplotUserEvent.EventType.LOGIN, event_date=TODAY)
        self.assertEqual(count_login_days(user), 3)
