"""复盘报告测试 (Issue 06).

覆盖: 通关自动生成报告 (知识总结 + 答题统计) / 幂等 / 统计与 Attempt
一致 / slug 唯一 / OG 文本与缩略图 / 报告 API (归属/未通关 404) /
公开分享页 (匿名可访问 + OG 标签 + 只读 + 未知 slug 404).
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app.charplot.models import (
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
    CharplotProfile,
    CharplotQuestion,
    CharplotReviewReport,
)
from app.charplot.services import (
    build_knowledge_summary,
    build_report_stats,
    create_review_report,
    ensure_levels_for_journey,
    generate_report_slug,
    submit_answer,
)

User = get_user_model()

TODAY = timezone.localdate()

SLUG_PATTERN = re.compile(r"^[a-hjkmnp-z2-9]{12}$")


def create_user(username="alice", password="TestPass#2026"):
    user = User.objects.create_user(username=username, password=password)
    CharplotProfile.objects.create(user=user)  # 注册流程自动创建, 测试直建需补
    return user


def make_journey(user, kp_count=2, summary="核心知识点概述"):
    """工厂: 单章 journey + N 个线性依赖知识点 (与 test_quiz 同款)."""
    journey = CharplotJourney.objects.create(
        user=user, title="测试旅程", input_type="text"
    )
    chapter = CharplotChapter.objects.create(journey=journey, title="基础章节", order=0)
    kps = []
    for i in range(kp_count):
        kp = CharplotKnowledgePoint.objects.create(
            chapter=chapter, title=f"知识点{i + 1}", summary=summary, order=i
        )
        if kps:
            kp.prerequisites.add(kps[-1])
        kps.append(kp)
    return journey, chapter, kps


def answers_for(level, correct=True):
    """按当前题构造答案 (与 test_quiz 同款)."""
    question = level.questions.order_by("order", "id")[level.current_index]
    if question.question_type == CharplotQuestion.QuestionType.CHOICE:
        return question.answer if correct else ([0] if question.answer[0] != 0 else [1])
    if question.question_type == CharplotQuestion.QuestionType.JUDGE:
        return (
            question.answer
            if correct
            else (["false"] if question.answer[0] == "true" else ["true"])
        )
    return question.answer if correct else ["完全错误"]


def clear_level(level, correct_pattern):
    """按模式答完一关, 返回该关最终提交结果."""
    for correct in correct_pattern:
        question = level.questions.order_by("order", "id")[level.current_index]
        result = submit_answer(
            level, question.id, answers_for(level, correct), duration=3, today=TODAY
        )
    return result


def clear_journey(journey, correct_pattern):
    """通关旅程全部关卡, 返回最终结果列表."""
    results = []
    for level in journey.levels.all():
        results.append(clear_level(level, correct_pattern))
    return results


# ---------------------------------------------------------------------------
# slug
# ---------------------------------------------------------------------------


class ReportSlugTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, _, _ = make_journey(self.user, kp_count=1)
        ensure_levels_for_journey(self.journey)

    def test_slug_matches_alphabet_and_length(self):
        slug = generate_report_slug()
        self.assertRegex(slug, SLUG_PATTERN)
        self.assertEqual(len(slug), 12)

    def test_slug_unique_across_generation(self):
        slugs = {generate_report_slug() for _ in range(20)}
        self.assertEqual(len(slugs), 20)  # 撞库重试保证唯一


# ---------------------------------------------------------------------------
# 统计与知识总结
# ---------------------------------------------------------------------------


class ReportAggregationTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, _, _ = make_journey(self.user, kp_count=2)
        ensure_levels_for_journey(self.journey)

    def test_stats_match_attempts(self):
        # 第一关: 5 对 1 错; 第二关: 全对
        clear_level(self.journey.levels.all()[0], [True, True, False, True, True, True])
        clear_level(self.journey.levels.all()[1], [True] * 6)
        stats = build_report_stats(self.journey)
        self.assertEqual(stats["answered"], 12)
        self.assertEqual(stats["correct"], 11)
        self.assertEqual(stats["wrong"], 1)
        self.assertEqual(stats["accuracy"], 92)  # round(11*100/12)
        self.assertEqual(stats["duration"], 36)  # 12 题, 每题 3s
        self.assertEqual(len(stats["levels"]), 2)
        first = stats["levels"][0]
        self.assertEqual(first["kp_title"], "知识点1")
        self.assertEqual(first["answered"], 6)
        self.assertEqual(first["correct"], 5)

    def test_stats_with_restart_history_included(self):
        # 关卡重开的历史 Attempt 一并计入 (与 profile 统计同源)
        level = self.journey.levels.first()
        clear_level(level, [False] * 5)  # 5 错扣完心 (failed)
        from app.charplot.services import restart_level

        restart_level(level)
        clear_level(level, [True] * 6)
        stats = build_report_stats(self.journey)
        self.assertEqual(stats["answered"], 11)
        self.assertEqual(stats["correct"], 6)

    def test_knowledge_summary_matches_graph(self):
        summary = build_knowledge_summary(self.journey)
        self.assertEqual(len(summary["chapters"]), 1)
        chapter = summary["chapters"][0]
        self.assertEqual(chapter["title"], "基础章节")
        self.assertEqual(len(chapter["knowledge_points"]), 2)
        self.assertEqual(
            [kp["title"] for kp in chapter["knowledge_points"]],
            ["知识点1", "知识点2"],
        )


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------


class CreateReportTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, _, _ = make_journey(self.user, kp_count=2)
        ensure_levels_for_journey(self.journey)

    def test_report_auto_generated_on_journey_clear(self):
        self.assertFalse(CharplotReviewReport.objects.exists())
        clear_journey(self.journey, [True] * 6)
        report = CharplotReviewReport.objects.get(journey=self.journey)
        self.assertEqual(report.user, self.user)
        self.assertRegex(report.slug, SLUG_PATTERN)
        # 快照数据与 Attempt 一致 (验收: 报告数据与 Attempt 一致)
        self.assertEqual(report.stats["answered"], 12)
        self.assertEqual(report.stats["correct"], 12)
        self.assertEqual(report.stats["accuracy"], 100)
        self.assertEqual(len(report.knowledge_summary["chapters"]), 1)
        # OG 文本
        self.assertIn("测试旅程", report.og_title)
        self.assertIn("100%", report.og_description)

    def test_no_report_before_clear(self):
        clear_level(self.journey.levels.first(), [True] * 6)
        self.assertFalse(CharplotReviewReport.objects.exists())

    def test_create_idempotent(self):
        clear_journey(self.journey, [True] * 6)
        first = CharplotReviewReport.objects.get(journey=self.journey)
        again = create_review_report(self.journey)
        self.assertEqual(again.id, first.id)
        self.assertEqual(CharplotReviewReport.objects.count(), 1)

    def test_og_image_generated_on_windows_font_env(self):
        clear_journey(self.journey, [True] * 6)
        report = CharplotReviewReport.objects.get(journey=self.journey)
        self.assertIn(report.slug, report.og_image)
        self.assertTrue(report.og_image.startswith("/media/app/charplot/uploads/og/"))
        # 实际文件存在
        import os

        from django.conf import settings

        rel_path = report.og_image.removeprefix("/media/")
        path = os.path.join(settings.MEDIA_ROOT, rel_path)
        self.assertTrue(os.path.exists(path), f"OG 图未生成: {path}")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class ReportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.client.force_login(self.user)
        self.journey, _, _ = make_journey(self.user, kp_count=1)
        ensure_levels_for_journey(self.journey)
        self.url = f"/api/charplot/journeys/{self.journey.id}/report/"

    def test_report_404_before_clear(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_report_after_clear(self):
        clear_journey(self.journey, [True] * 6)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["journey_id"], self.journey.id)
        self.assertEqual(data["stats"]["answered"], 6)
        self.assertIn("knowledge_summary", data)
        self.assertIn("share_url", data)
        self.assertRegex(data["share_url"], r"^/r/[a-hjkmnp-z2-9]{12}/$")

    def test_other_user_report_404(self):
        other = create_user("bob")
        other_journey, _, _ = make_journey(other, kp_count=1)
        ensure_levels_for_journey(other_journey)
        clear_journey(other_journey, [True] * 6)
        resp = self.client.get(f"/api/charplot/journeys/{other_journey.id}/report/")
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_forbidden(self):
        anon = APIClient()
        resp = anon.get(self.url)
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# 公开分享页 (PRD E-2)
# ---------------------------------------------------------------------------


class SharePageTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, _, _ = make_journey(self.user, kp_count=2)
        ensure_levels_for_journey(self.journey)
        clear_journey(self.journey, [True] * 6)
        self.report = CharplotReviewReport.objects.get(journey=self.journey)

    def test_anonymous_can_access_share_page(self):
        anon = APIClient()
        resp = anon.get(f"/r/{self.report.slug}/")
        self.assertEqual(resp.status_code, 200)

    def test_share_page_contains_report_content(self):
        resp = self.client.get(f"/r/{self.report.slug}/")
        html = resp.content.decode("utf-8")
        # 知识总结 + 答题统计 (与报告快照一致)
        self.assertIn("基础章节", html)
        self.assertIn("知识点1", html)
        self.assertIn("100%", html)
        self.assertIn("答题表现", html)
        self.assertIn("知识总结", html)

    def test_og_tags_present(self):
        resp = self.client.get(f"/r/{self.report.slug}/")
        html = resp.content.decode("utf-8")
        self.assertIn('property="og:title"', html)
        self.assertIn("测试旅程 · 通关复盘", html)
        self.assertIn('property="og:description"', html)
        self.assertIn('property="og:image"', html)
        self.assertIn(f"/media/app/charplot/uploads/og/{self.report.slug}.png", html)
        self.assertIn("twitter:card", html)

    def test_unknown_slug_404(self):
        resp = self.client.get("/r/no-such-slug-xxxx/")
        self.assertEqual(resp.status_code, 404)

    def test_share_page_read_only(self):
        # 分享页仅 GET 展示, 无写端点: 其余方法不允许
        anon = APIClient()
        resp = anon.post(f"/r/{self.report.slug}/", {"anything": 1})
        self.assertEqual(resp.status_code, 405)
