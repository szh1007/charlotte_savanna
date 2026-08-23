"""分析 Dashboard 测试 (Issue 12).

覆盖: 掌握度矩阵 (知识点/章节聚合与 Attempt 一致, 薄弱点高亮, 复习题
归属来源知识点) / 活动统计 (时长/通关数/活跃天数与事件表一致, 近 N 天
分布) / 易错清单 (优先级公式与间隔复习同源, 排序与分档) / API 权限
(未登录 401, 数据按用户隔离).
"""

import datetime
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app.charplot.dashboard import (
    build_activity_stats,
    build_mastery_matrix,
    build_weakpoint_list,
)
from app.charplot.models import (
    CharplotAttempt,
    CharplotProfile,
    CharplotUserEvent,
)
from app.charplot.services import _review_candidates
from app.charplot.tests.test_quiz import (
    answer_all,
    create_user,
    make_journey,
    make_level_ready,
)

User = get_user_model()

TODAY = timezone.localdate()


def make_answered_level(user, journey, kp, correct_pattern, days_ago=0):
    """工厂: 建就绪关并按题序作答 (事实落入 Attempt / 用户事件 / 易错分).

    days_ago 控制事件日期偏移 (近 N 天分布断言用); 错题数 ≤ 5 (心动值上限).
    """
    level = make_level_ready(journey, kp=kp, count=len(correct_pattern))
    answer_all(level, correct_pattern, today=TODAY - timedelta(days=days_ago))
    return level


def at_local_noon(date):
    """本地正午 aware datetime: .date() 与 localdate() 结果一致 (UTC 跨天
    歧义测试用, 上午 UTC 时刻两实现会差 1 天)."""
    return timezone.make_aware(datetime.datetime.combine(date, datetime.time(12, 0)))


# ---------------------------------------------------------------------------
# 掌握度矩阵
# ---------------------------------------------------------------------------


class MasteryMatrixTests(TestCase):
    def setUp(self):
        self.user = create_user("alice")

    def test_aggregates_match_attempt_fact_table(self):
        journey, _, kps = make_journey(self.user, kp_count=2)
        make_answered_level(self.user, journey, kps[0], [True, False, True])
        make_answered_level(self.user, journey, kps[1], [False, False, True])

        journeys = build_mastery_matrix(self.user)["journeys"]
        self.assertEqual(len(journeys), 1)
        self.assertEqual(len(journeys[0]["chapters"]), 1)
        points = journeys[0]["chapters"][0]["knowledge_points"]
        self.assertEqual(len(points), 2)

        # 与 Attempt 事实表逐一比对 (验收: 数字与 Attempt 一致)
        for point in points:
            attempts = CharplotAttempt.objects.filter(
                user=self.user, level__knowledge_point_id=point["kp_id"]
            )
            self.assertEqual(point["answered"], attempts.count())
            self.assertEqual(point["correct"], attempts.filter(is_correct=True).count())
            self.assertEqual(
                point["accuracy"], round(point["correct"] * 100 / point["answered"])
            )
            self.assertEqual(
                point["duration"],
                attempts.aggregate(d=Sum("duration"))["d"] or 0,
            )

    def test_weak_highlight_below_threshold(self):
        journey, _, kps = make_journey(self.user, kp_count=2)
        make_answered_level(self.user, journey, kps[0], [True, True, True])
        make_answered_level(self.user, journey, kps[1], [False, False, True])

        points = build_mastery_matrix(self.user)["journeys"][0]["chapters"][0][
            "knowledge_points"
        ]
        by_id = {p["kp_id"]: p for p in points}
        self.assertFalse(by_id[kps[0].id]["weak"])  # 100% 非薄弱
        self.assertTrue(by_id[kps[1].id]["weak"])  # 33% 薄弱高亮

    def test_chapter_stats_rollup_of_points(self):
        journey, _, kps = make_journey(self.user, kp_count=2)
        make_answered_level(self.user, journey, kps[0], [True, True])
        make_answered_level(self.user, journey, kps[1], [False, True, True])

        chapter_stat = build_mastery_matrix(self.user)["journeys"][0]["chapters"][0]
        self.assertEqual(chapter_stat["answered"], 5)
        self.assertEqual(chapter_stat["correct"], 4)
        self.assertEqual(chapter_stat["accuracy"], 80)
        self.assertEqual(
            chapter_stat["duration"],
            sum(p["duration"] for p in chapter_stat["knowledge_points"]),
        )

    def test_review_question_attributed_to_source_kp(self):
        # 复习题 (source_kp): 答错易错分记来源知识点, 掌握度同样归来源
        journey, _, kps = make_journey(self.user, kp_count=2)
        source = make_answered_level(self.user, journey, kps[0], [True, False, True])
        review_level = make_level_ready(journey, kp=kps[1], count=2)
        # 把第 1 题改为来源知识点复习题 (复制题, 与 Issue 08 生成逻辑一致)
        question = review_level.questions.first()
        question.source_kp = kps[0]
        question.save(update_fields=["source_kp"])
        answer_all(review_level, [True, True], today=TODAY)

        points = build_mastery_matrix(self.user)["journeys"][0]["chapters"][0][
            "knowledge_points"
        ]
        by_id = {p["kp_id"]: p for p in points}
        # kps[0]: 3 题 (2 对 1 错) + 1 道复习题答对 = 4 题 3 对
        self.assertEqual(by_id[kps[0].id]["answered"], 4)
        self.assertEqual(by_id[kps[0].id]["correct"], 3)
        # kps[1]: 仅 1 道常规题答对
        self.assertEqual(by_id[kps[1].id]["answered"], 1)
        self.assertEqual(by_id[kps[1].id]["correct"], 1)
        self.assertEqual(source.knowledge_point_id, kps[0].id)

    def test_empty_without_attempts(self):
        create_user("bob")
        self.assertEqual(build_mastery_matrix(self.user)["journeys"], [])

    def test_other_users_data_isolated(self):
        other = create_user("bob")
        journey, _, kps = make_journey(self.user, kp_count=1)
        make_answered_level(self.user, journey, kps[0], [True])
        self.assertEqual(build_mastery_matrix(other)["journeys"], [])


# ---------------------------------------------------------------------------
# 学习活动统计
# ---------------------------------------------------------------------------


class ActivityStatsTests(TestCase):
    def setUp(self):
        self.user = create_user("alice")
        self.profile = CharplotProfile.objects.get(user=self.user)

    def test_stats_derive_from_fact_tables(self):
        # Attempt: 3 条, duration 5/10/15 (count=4 只答 3 题, 不触发通关结算)
        journey, _, kps = make_journey(self.user, kp_count=1)
        level = make_level_ready(journey, kp=kps[0], count=4)
        answer_all(level, [True, False, True], today=TODAY)
        CharplotAttempt.objects.filter(user=self.user).update(duration=5)
        # 逐条覆盖时长避免依赖工厂默认值
        for attempt, dur in zip(
            CharplotAttempt.objects.filter(user=self.user).order_by("id"),
            [5, 10, 15],
        ):
            attempt.duration = dur
            attempt.save(update_fields=["duration"])

        # 事件: 2 次通关 (今天 + 3 天前) + 3 个登录日 (含跨窗口)
        CharplotUserEvent.objects.create(
            user=self.user,
            event_type=CharplotUserEvent.EventType.LEVEL_CLEAR,
            event_date=TODAY - timedelta(days=3),
        )
        CharplotUserEvent.objects.create(
            user=self.user,
            event_type=CharplotUserEvent.EventType.LEVEL_CLEAR,
            event_date=TODAY,
        )
        for offset in [0, 1, 5]:
            CharplotUserEvent.objects.create(
                user=self.user,
                event_type=CharplotUserEvent.EventType.LOGIN,
                event_date=TODAY - timedelta(days=offset),
            )

        self.profile.streak = 4
        self.profile.max_streak = 9
        self.profile.save(update_fields=["streak", "max_streak"])

        stats = build_activity_stats(self.user)
        self.assertEqual(stats["duration_seconds"], 30)  # 5+10+15
        self.assertEqual(stats["cleared_levels"], 2)  # LEVEL_CLEAR 事件行数
        self.assertEqual(stats["active_days"], 3)  # LOGIN 按日去重
        self.assertEqual(stats["streak"], 4)
        self.assertEqual(stats["max_streak"], 9)

    def test_daily_window_continuous_with_active_flags(self):
        journey, _, kps = make_journey(self.user, kp_count=1)
        make_answered_level(self.user, journey, kps[0], [True, True], days_ago=3)
        make_answered_level(self.user, journey, kps[0], [True], days_ago=0)

        stats = build_activity_stats(self.user)
        daily = stats["daily"]
        self.assertEqual(len(daily), 14)  # 近 14 天窗口 (含今日)
        self.assertEqual(daily[0]["date"], (TODAY - timedelta(days=13)).isoformat())
        self.assertEqual(daily[-1]["date"], TODAY.isoformat())

        by_date = {d["date"]: d for d in daily}
        today_entry = by_date[TODAY.isoformat()]
        ago3 = by_date[(TODAY - timedelta(days=3)).isoformat()]
        self.assertTrue(today_entry["active"])
        self.assertEqual(today_entry["answers"], 1)
        self.assertTrue(ago3["active"])
        self.assertEqual(ago3["answers"], 2)
        # 无学习行为的日期补零且不活跃
        idle = by_date[(TODAY - timedelta(days=1)).isoformat()]
        self.assertFalse(idle["active"])
        self.assertEqual(idle["answers"], 0)

    def test_empty_user_zeroed(self):
        stats = build_activity_stats(self.user)
        self.assertEqual(stats["duration_seconds"], 0)
        self.assertEqual(stats["cleared_levels"], 0)
        self.assertEqual(stats["active_days"], 0)
        self.assertEqual(stats["streak"], 0)
        self.assertTrue(all(not d["active"] for d in stats["daily"]))


# ---------------------------------------------------------------------------
# 易错点清单
# ---------------------------------------------------------------------------


class WeakpointListTests(TestCase):
    def setUp(self):
        self.user = create_user("alice")

    def test_ranked_by_priority_with_review_decay(self):
        journey, _, kps = make_journey(self.user, kp_count=3)
        # 易错分 (答错 +2 / 答对 -1): kp1=4 (3 错 2 对), kp2=7 (4 错 1 对),
        # kp3=10 (5 错全错). 复习衰减: kp2 最近复习过 (1 天前), 其余从未
        # 复习 (REVIEW_NEVER_DAYS=30 天计)
        make_answered_level(self.user, journey, kps[0], [False] * 3 + [True] * 2)
        make_answered_level(self.user, journey, kps[1], [False] * 4 + [True])
        make_answered_level(self.user, journey, kps[2], [False] * 5)
        # 本地正午 1 天前 (aware datetime, 消除 UTC 跨天日期歧义)
        kps[1].last_reviewed_at = at_local_noon(TODAY - timedelta(days=1))
        kps[1].save(update_fields=["last_reviewed_at"])

        weakpoints = build_weakpoint_list(self.user)["weakpoints"]
        self.assertEqual(len(weakpoints), 3)
        # priority = error_score * (days + 1)
        by_id = {w["kp_id"]: w for w in weakpoints}
        self.assertEqual(by_id[kps[0].id]["priority"], 4 * (30 + 1))  # 从未复习
        self.assertEqual(by_id[kps[1].id]["priority"], 7 * (1 + 1))  # 刚复习过
        self.assertEqual(by_id[kps[2].id]["priority"], 10 * (30 + 1))
        # 排序: priority 降序 → error_score 降序 → id 升序;
        # kp1 分低于 kp2 但未复习排前 (时间衰减生效)
        order = [w["kp_id"] for w in weakpoints]
        self.assertEqual(order, [kps[2].id, kps[0].id, kps[1].id])
        # wrong_count = 名下答错 Attempt 数 (含复习题归属)
        self.assertEqual(by_id[kps[0].id]["wrong_count"], 3)
        self.assertEqual(by_id[kps[1].id]["wrong_count"], 4)
        self.assertEqual(by_id[kps[2].id]["wrong_count"], 5)

    def test_same_formula_as_review_candidates(self):
        """与间隔复习同源: 同一旅程候选排序与 _review_candidates 一致."""
        journey, _, kps = make_journey(self.user, kp_count=3)
        make_answered_level(self.user, journey, kps[0], [False, False, True])
        make_answered_level(self.user, journey, kps[1], [False, True, True])
        make_answered_level(self.user, journey, kps[2], [False, True, True])
        kps[1].last_reviewed_at = at_local_noon(TODAY - timedelta(days=2))
        kps[1].save(update_fields=["last_reviewed_at"])

        weakpoints = build_weakpoint_list(self.user)["weakpoints"]
        candidates = _review_candidates(journey, exclude_kp_ids=set(), today=TODAY)
        self.assertEqual([w["kp_id"] for w in weakpoints], [kp.id for kp in candidates])

    def test_priority_level_tiers(self):
        journey, _, kps = make_journey(self.user, kp_count=5)
        # 易错分各不同 (score = 2*错数 - 对数): 3 / 4 / 6 / 7 / 10,
        # 错题数 ≤ 5 (心动值上限); 5 错全答完无后续提交, 不触发重开
        patterns = [
            [False, False, True],  # 2 错 1 对 → 3
            [False] * 3 + [True] * 2,  # 3 错 2 对 → 4
            [False] * 4 + [True] * 2,  # 4 错 2 对 → 6
            [False] * 4 + [True],  # 4 错 1 对 → 7
            [False] * 5,  # 5 错 → 10
        ]
        for kp, pattern in zip(kps, patterns):
            make_answered_level(self.user, journey, kp, pattern)

        weakpoints = build_weakpoint_list(self.user)["weakpoints"]
        # error_score 降序: kp5(10) > kp4(7) > kp3(6) > kp2(4) > kp1(3)
        self.assertEqual(
            [w["kp_id"] for w in weakpoints],
            [kps[4].id, kps[3].id, kps[2].id, kps[1].id, kps[0].id],
        )
        # 5 项三等分 (ceil): 前 2 高 / 中 2 中 / 后 1 低
        self.assertEqual(weakpoints[0]["priority_level"], "high")
        self.assertEqual(weakpoints[1]["priority_level"], "high")
        self.assertEqual(weakpoints[2]["priority_level"], "medium")
        self.assertEqual(weakpoints[3]["priority_level"], "medium")
        self.assertEqual(weakpoints[4]["priority_level"], "low")

    def test_no_error_points_returns_empty(self):
        journey, _, kps = make_journey(self.user, kp_count=1)
        make_answered_level(self.user, journey, kps[0], [True, True])
        self.assertEqual(build_weakpoint_list(self.user)["weakpoints"], [])

    def test_other_users_weakpoints_isolated(self):
        other = create_user("bob")
        journey, _, kps = make_journey(other, kp_count=1)
        make_answered_level(other, journey, kps[0], [False, False])
        # alice 无任何易错知识点, 即使他人有 (回归: 全局查询曾泄露他人数据)
        self.assertEqual(build_weakpoint_list(self.user)["weakpoints"], [])
        self.assertEqual(len(build_weakpoint_list(other)["weakpoints"]), 1)


# ---------------------------------------------------------------------------
# API 权限与响应
# ---------------------------------------------------------------------------


class DashboardApiTests(TestCase):
    def setUp(self):
        self.user = create_user("alice")
        self.client = APIClient()

    DASHBOARD_PATHS = [
        "/api/charplot/dashboard/mastery/",
        "/api/charplot/dashboard/activity/",
        "/api/charplot/dashboard/weakpoints/",
    ]

    def test_requires_login(self):
        # SessionAuthentication 未登录返回 403 (与 profile 等视图同惯例)
        for path in self.DASHBOARD_PATHS:
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_authenticated_returns_aggregates(self):
        self.client.force_authenticate(self.user)
        for path in self.DASHBOARD_PATHS:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(
            self.client.get("/api/charplot/dashboard/mastery/").data,
            {"journeys": []},
        )
        self.assertEqual(
            self.client.get("/api/charplot/dashboard/weakpoints/").data,
            {"weakpoints": []},
        )
        activity = self.client.get("/api/charplot/dashboard/activity/").data
        self.assertIn("duration_seconds", activity)
        self.assertIn("daily", activity)
