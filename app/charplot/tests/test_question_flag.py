"""题目反馈标记测试 (Issue 14, SPEC §7.3 ③ 幻觉防护第三层).

覆盖: 落库 (题目/用户/原因/时间) / 同一用户重复标记去重 (unique
constraint, 原记录保留) / 不同用户可标记同一题 / 原因可选 (空 = 仅标记) /
API 权限与归属校验 (未登录 403, 他人旅程题目 404 不泄露存在性) /
LevelDetail 当前题 flagged 持久化 (重进答题页恢复已反馈状态).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from app.charplot.models import (
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
    CharplotLevel,
    CharplotProfile,
    CharplotQuestion,
    CharplotQuestionFlag,
)
from app.charplot.services import flag_question

User = get_user_model()


def create_user(username="alice", password="TestPass#2026"):
    user = User.objects.create_user(username=username, password=password)
    CharplotProfile.objects.create(user=user)  # 注册流程自动创建, 测试直建需补
    return user


def make_question(user, content="选择题题干"):
    """工厂: 单个 ready 关卡 + 一道选择题, 返回 (journey, question)."""
    journey = CharplotJourney.objects.create(
        user=user, title="测试旅程", input_type="text"
    )
    chapter = CharplotChapter.objects.create(journey=journey, title="基础章节", order=0)
    kp = CharplotKnowledgePoint.objects.create(
        chapter=chapter, title="知识点1", summary="概述", order=0
    )
    level = CharplotLevel.objects.create(
        journey=journey,
        knowledge_point=kp,
        chapter=chapter,
        seq=1,
        questions_status=CharplotLevel.QuestionsStatus.READY,
    )
    question = CharplotQuestion.objects.create(
        level=level,
        question_type=CharplotQuestion.QuestionType.CHOICE,
        content=content,
        options=["正确选项", "干扰项"],
        answer=[0],
        explanation="讲解",
        order=0,
    )
    return journey, question


class FlagServiceTests(TestCase):
    """服务层: 落库字段 / 去重 / 原因可选."""

    def setUp(self):
        self.user = create_user()
        self.other = create_user(username="bob")
        _, self.question = make_question(self.user)

    def test_flag_creates_record_with_reason_and_time(self):
        flag, created = flag_question(
            self.question, self.user, CharplotQuestionFlag.Reason.ANSWER_ERROR
        )
        self.assertTrue(created)
        self.assertEqual(flag.question, self.question)
        self.assertEqual(flag.user, self.user)
        self.assertEqual(flag.reason, CharplotQuestionFlag.Reason.ANSWER_ERROR)
        self.assertIsNotNone(flag.created_at)  # auto_now_add 落时间

    def test_flag_without_reason_allowed(self):
        flag, created = flag_question(self.question, self.user, reason="")
        self.assertTrue(created)
        self.assertEqual(flag.reason, "")

    def test_duplicate_flag_same_user_dedupes(self):
        flag_question(self.question, self.user, reason="answer_error")
        flag, created = flag_question(
            self.question, self.user, reason="explanation_error"
        )
        # 去重: 不新建, 原记录保留 (reason 不覆盖)
        self.assertFalse(created)
        self.assertEqual(flag.reason, CharplotQuestionFlag.Reason.ANSWER_ERROR)
        self.assertEqual(
            CharplotQuestionFlag.objects.filter(
                question=self.question, user=self.user
            ).count(),
            1,
        )

    def test_different_users_can_flag_same_question(self):
        flag_question(self.question, self.user)
        flag_question(self.question, self.other)
        # 去重粒度为 (题目, 用户): 两人各一条
        self.assertEqual(
            CharplotQuestionFlag.objects.filter(question=self.question).count(), 2
        )


class FlagApiTests(TestCase):
    """API: 权限 / 归属校验 / 幂等 / 序列化 flagged."""

    def setUp(self):
        self.user = create_user()
        self.other = create_user(username="bob")
        _, self.question = make_question(self.user)
        _, other_question = make_question(self.other, content="他人旅程题目")
        self.other_question = other_question
        self.url = f"/api/charplot/questions/{self.question.id}/flag/"
        self.client.force_login(self.user)

    def test_flag_requires_login(self):
        resp = APIClient().post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_flag_creates_and_returns_created(self):
        resp = self.client.post(
            self.url,
            {"reason": CharplotQuestionFlag.Reason.ANSWER_ERROR},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["created"])
        self.assertEqual(
            CharplotQuestionFlag.objects.filter(
                question=self.question, user=self.user
            ).count(),
            1,
        )

    def test_duplicate_flag_returns_created_false(self):
        self.client.post(self.url, {}, format="json")
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["created"])

    def test_flag_without_reason_ok(self):
        resp = self.client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["created"])

    def test_flag_invalid_reason_400(self):
        resp = self.client.post(self.url, {"reason": "bogus"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_flag_question_not_found_404(self):
        url = "/api/charplot/questions/99999/flag/"
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_flag_other_users_question_404(self):
        # 归属校验: 非本人旅程题目 404, 不泄露存在性 (同关卡/旅程详情)
        resp = self.client.post(
            f"/api/charplot/questions/{self.other_question.id}/flag/",
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_level_detail_flagged_persists(self):
        # 未标记: flagged=False
        resp = self.client.get(f"/api/charplot/levels/{self.question.level_id}/")
        self.assertEqual(resp.data["question"]["flagged"], False)
        # 标记后重进: flagged=True (前端恢复「已反馈」状态)
        self.client.post(self.url, {}, format="json")
        resp = self.client.get(f"/api/charplot/levels/{self.question.level_id}/")
        self.assertEqual(resp.data["question"]["flagged"], True)

    def test_level_detail_flagged_scoped_to_user(self):
        # 他人标记不影响本用户视图 (flagged 按当前用户计算)
        flag_question(self.question, self.other)
        resp = self.client.get(f"/api/charplot/levels/{self.question.level_id}/")
        self.assertEqual(resp.data["question"]["flagged"], False)
