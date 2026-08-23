"""闯关答题测试 (Issue 05/08).

覆盖: 关卡创建 (每 kp 一关 + 章末 boss 关, 题目渐进生成 pending) / 判分
(选择/判断/填空归一化) / 答题结算 (XP/心动值/易错分/事件) / 通关结算
(XP/币/连胜/点亮) / 断点续答 / 5 心扣完重开 (Attempt 保留) / API 权限与
防重放. Issue 08 后题目由 FastAPI 任务生成, 测试用 make_level_ready 工厂
直建就绪关卡 (题目生成任务链路见 test_level_generation.py).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app.charplot.models import (
    CharplotAttempt,
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
    CharplotLevel,
    CharplotProfile,
    CharplotQuestion,
    CharplotUserEvent,
)
from app.charplot.services import (
    ANSWER_CORRECT_XP,
    LEVEL_CLEAR_COINS,
    LEVEL_CLEAR_XP,
    MAX_HEARTS,
    LevelClearedError,
    LevelFailedError,
    LevelNotCurrentError,
    check_answer,
    ensure_levels_for_journey,
    level_locked,
    level_status,
    normalize_answer,
    restart_level,
    submit_answer,
)

User = get_user_model()

TODAY = timezone.localdate()


def create_user(username="alice", password="TestPass#2026"):
    user = User.objects.create_user(username=username, password=password)
    CharplotProfile.objects.create(user=user)  # 注册流程自动创建, 测试直建需补
    return user


def make_journey(user, kp_count=2, summary="核心知识点概述"):
    """工厂: 单章 journey + N 个线性依赖知识点."""
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


def make_level_ready(journey, kp=None, count=6, level_type="regular"):
    """工厂 (Issue 08): 直建就绪关卡 + 混合题型题目 (内容合法, 可答题).

    题目渐进生成后由 FastAPI 落库, 服务层/判分测试不依赖生成链路, 直接
    建 ready 关 + 3 种题型题目 (选择/判断/填空循环), 返回关卡.
    """
    kp = kp or journey.chapters.first().knowledge_points.first()
    level = CharplotLevel.objects.create(
        journey=journey,
        knowledge_point=kp,
        chapter=kp.chapter,
        seq=journey.levels.count() + 1,
        level_type=level_type,
        questions_status=CharplotLevel.QuestionsStatus.READY,
    )
    types = [
        CharplotQuestion.QuestionType.CHOICE,
        CharplotQuestion.QuestionType.JUDGE,
        CharplotQuestion.QuestionType.FILL,
    ]
    for order in range(count):
        qtype = types[order % len(types)]
        if qtype == CharplotQuestion.QuestionType.CHOICE:
            data = {
                "question_type": qtype,
                "content": f"选择题题干 {order + 1}",
                "options": [f"正确选项{order + 1}", "干扰项A", "干扰项B", "干扰项C"],
                "answer": [0],
                "explanation": "选择题讲解",
            }
        elif qtype == CharplotQuestion.QuestionType.JUDGE:
            data = {
                "question_type": qtype,
                "content": f"判断题题干 {order + 1}",
                "options": [],
                "answer": ["true"],
                "explanation": "判断题讲解",
            }
        else:
            data = {
                "question_type": qtype,
                "content": f"填空题题干 {order + 1}: ____",
                "options": [],
                "answer": [f"标准答案{order + 1}"],
                "explanation": "填空题讲解",
            }
        CharplotQuestion.objects.create(level=level, order=order, **data)
    return level


def answers_for(level, correct=True, wrong=True):
    """按当前题构造答案: 选择取答案下标, 判断取标准答案, 填空取归一化后文本."""
    question = level.questions.order_by("order", "id")[level.current_index]
    if question.question_type == CharplotQuestion.QuestionType.CHOICE:
        return question.answer if correct else ([0] if question.answer[0] != 0 else [1])
    if question.question_type == CharplotQuestion.QuestionType.JUDGE:
        return (
            question.answer
            if correct
            else (["false"] if question.answer[0] == "true" else ["true"])
        )
    # 填空: 标准答案文本
    return question.answer if correct else ["完全错误"]


def answer_all(level, correct_pattern, today=TODAY):
    """按题序作答 (correct_pattern 为布尔序列, 长度 ≤ 题数), 返回结果列表."""
    results = []
    for correct in correct_pattern:
        question = level.questions.order_by("order", "id")[level.current_index]
        answer = answers_for(level, correct)
        results.append(
            submit_answer(level, question.id, answer, duration=3, today=today)
        )
    return results


# ---------------------------------------------------------------------------
# 填空归一化
# ---------------------------------------------------------------------------


class NormalizeAnswerTests(TestCase):
    def test_strips_whitespace_and_lowercases(self):
        self.assertEqual(normalize_answer("  Python装饰器 "), "python装饰器")

    def test_full_width_to_half_width(self):
        # 全角空白 / 全角字母归一化 (全角字符为测试输入, 非笔误)
        self.assertEqual(normalize_answer("　ＰＹＴＨＯＮ　"), "python")  # noqa: RUF001

    def test_mixed_punctuation(self):
        self.assertEqual(normalize_answer("Django ORM!"), "djangoorm!")


# ---------------------------------------------------------------------------
# 关卡创建 (Issue 08: 空关待生成 + 章末 boss 关)
# ---------------------------------------------------------------------------


class EnsureLevelsTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, _, self.kps = make_journey(self.user)

    def test_creates_one_level_per_kp_plus_boss(self):
        levels = ensure_levels_for_journey(self.journey)
        # 每 kp 1 常规关 + 每章 1 boss 关
        self.assertEqual(len(levels), len(self.kps) + 1)
        self.assertEqual(self.journey.levels.count(), len(self.kps) + 1)

    def test_idempotent(self):
        ensure_levels_for_journey(self.journey)
        ensure_levels_for_journey(self.journey)
        self.assertEqual(self.journey.levels.count(), len(self.kps) + 1)

    def test_levels_pending_without_questions(self):
        # 题目渐进生成: 骨架空关 pending, 0 题, 由 FastAPI 任务填充
        ensure_levels_for_journey(self.journey)
        for level in self.journey.levels.all():
            self.assertEqual(
                level.questions_status, CharplotLevel.QuestionsStatus.PENDING
            )
            self.assertEqual(level.questions.count(), 0)

    def test_seq_increments_and_boss_after_regular(self):
        ensure_levels_for_journey(self.journey)
        levels = list(self.journey.levels.order_by("seq", "id"))
        self.assertEqual([level.seq for level in levels], [1, 2, 3])
        self.assertEqual(levels[-1].level_type, CharplotLevel.LevelType.BOSS)
        self.assertIsNotNone(levels[-1].chapter)
        self.assertFalse(level_locked(levels[0]))  # 第一章常规关永不锁定

    def test_boss_locked_until_regular_cleared(self):
        ensure_levels_for_journey(self.journey)
        boss = self.journey.levels.get(level_type=CharplotLevel.LevelType.BOSS)
        self.assertTrue(level_locked(boss))  # 章内常规关未通关 → boss 锁定


# ---------------------------------------------------------------------------
# 判分
# ---------------------------------------------------------------------------


class CheckAnswerTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, _, _ = make_journey(self.user)
        self.level = make_level_ready(self.journey)

    def q(self, question_type):
        return self.level.questions.filter(question_type=question_type).first()

    def test_choice_exact_match(self):
        question = self.q(CharplotQuestion.QuestionType.CHOICE)
        self.assertTrue(check_answer(question, question.answer))
        self.assertFalse(check_answer(question, [99]))

    def test_judge_exact_match(self):
        question = self.q(CharplotQuestion.QuestionType.JUDGE)
        self.assertTrue(check_answer(question, question.answer))
        opposite = ["false"] if question.answer[0] == "true" else ["true"]
        self.assertFalse(check_answer(question, opposite))

    def test_fill_fuzzy_match(self):
        question = self.q(CharplotQuestion.QuestionType.FILL)
        answer_text = question.answer[0]  # 工厂答案: 标准答案N
        self.assertTrue(check_answer(question, [f" {answer_text} "]))  # 去空白
        # 全角数字归一化 (NFKC); 全角字符为测试输入, 非笔误
        fullwidth = answer_text.translate(
            str.maketrans("0123456789", "０１２３４５６７８９")  # noqa: RUF001
        )
        self.assertTrue(check_answer(question, [fullwidth]))
        self.assertFalse(check_answer(question, ["完全不相关"]))

    def test_malformed_answer_is_wrong_not_error(self):
        question = self.q(CharplotQuestion.QuestionType.CHOICE)
        self.assertFalse(check_answer(question, []))
        self.assertFalse(check_answer(question, ["abc"]))


# ---------------------------------------------------------------------------
# 答题结算
# ---------------------------------------------------------------------------


class SubmitAnswerTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.profile = self.user.charplot_profile
        self.journey, _, self.kps = make_journey(self.user)
        self.level = make_level_ready(self.journey)
        self.kp = self.level.knowledge_point

    def current(self):
        return self.level.questions.order_by("order", "id")[self.level.current_index]

    def test_correct_answer_grants_xp_and_attempt(self):
        question = self.current()
        result = submit_answer(self.level, question.id, question.answer, duration=3)
        self.assertTrue(result["correct"])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.xp, ANSWER_CORRECT_XP)
        self.assertEqual(self.level.current_index, 1)
        self.assertEqual(self.level.hearts, MAX_HEARTS)
        attempt = CharplotAttempt.objects.get(level=self.level)
        self.assertTrue(attempt.is_correct)
        self.assertEqual(attempt.user_answer, question.answer)
        self.assertEqual(attempt.duration, 3)

    def test_wrong_answer_deducts_heart_and_syncs_profile(self):
        wrong = answers_for(self.level, correct=False)
        result = submit_answer(self.level, self.current().id, wrong)
        self.assertFalse(result["correct"])
        self.assertEqual(result["hearts"], MAX_HEARTS - 1)
        self.assertEqual(self.level.hearts, MAX_HEARTS - 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.hearts, MAX_HEARTS - 1)
        self.assertEqual(self.profile.xp, 0)

    def test_error_score_updates(self):
        # 答错 +2
        submit_answer(self.level, self.current().id, answers_for(self.level, False))
        self.kp.refresh_from_db()
        self.assertEqual(self.kp.error_score, 2)
        # 答对 -1
        submit_answer(self.level, self.current().id, self.current().answer)
        self.kp.refresh_from_db()
        self.assertEqual(self.kp.error_score, 1)
        # 下限 0: 0 分时答对不再减
        for _ in range(3):
            submit_answer(self.level, self.current().id, self.current().answer)
        self.kp.refresh_from_db()
        self.assertEqual(self.kp.error_score, 0)

    def test_answer_event_recorded_per_question(self):
        submit_answer(self.level, self.current().id, self.current().answer)
        submit_answer(self.level, self.current().id, answers_for(self.level, False))
        events = CharplotUserEvent.objects.filter(
            user=self.user, event_type=CharplotUserEvent.EventType.ANSWER
        )
        self.assertEqual(events.count(), 2)  # 逐条落库, 不按日去重
        self.assertTrue(all(e.payload["correct"] in (True, False) for e in events))

    def test_replay_rejected(self):
        first = self.current()
        submit_answer(self.level, first.id, first.answer)
        with self.assertRaises(LevelNotCurrentError):
            submit_answer(self.level, first.id, first.answer)

    def test_cleared_level_rejected(self):
        answer_all(self.level, [True] * 6)
        first = self.level.questions.order_by("order", "id")[0]
        with self.assertRaises(LevelClearedError):
            submit_answer(self.level, first.id, first.answer)


class LevelClearTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.profile = self.user.charplot_profile
        self.journey, _, self.kps = make_journey(self.user)
        self.level = make_level_ready(self.journey)

    def current(self):
        return self.level.questions.order_by("order", "id")[self.level.current_index]

    def test_clear_all_correct(self):
        results = answer_all(self.level, [True] * 6)
        last = results[-1]
        self.assertTrue(last["cleared"])
        self.assertEqual(last["level_status"], "cleared")
        self.assertEqual(last["reward"]["xp"], LEVEL_CLEAR_XP)
        self.assertEqual(last["reward"]["coins"], LEVEL_CLEAR_COINS)
        self.assertEqual(last["reward"]["streak"], 1)
        self.level.refresh_from_db()
        self.assertTrue(self.level.cleared)
        self.profile.refresh_from_db()
        # 6 题答对 + 通关奖励
        self.assertEqual(self.profile.xp, 6 * ANSWER_CORRECT_XP + LEVEL_CLEAR_XP)
        self.assertEqual(self.profile.coins, LEVEL_CLEAR_COINS)
        self.assertEqual(self.profile.streak, 1)
        self.assertEqual(self.profile.last_study_date, TODAY)

    def test_clear_with_some_wrong(self):
        results = answer_all(self.level, [True, True, False, True, True, True])
        self.assertTrue(results[-1]["cleared"])
        self.profile.refresh_from_db()
        # 5 对 1 错: 5*10 + 50, 心剩 4
        self.assertEqual(self.profile.xp, 5 * ANSWER_CORRECT_XP + LEVEL_CLEAR_XP)
        self.level.refresh_from_db()
        self.assertEqual(self.level.hearts, MAX_HEARTS - 1)

    def test_streak_increments_on_consecutive_days(self):
        answer_all(self.level, [True] * 6, today=TODAY)
        # 第二天继续学另一个关卡 → streak=2
        second = make_level_ready(self.journey, self.kps[1])
        answer_all(second, [True] * 6, today=TODAY + timedelta(days=1))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.streak, 2)
        self.assertEqual(self.profile.max_streak, 2)

    def test_streak_breaks_after_gap(self):
        answer_all(self.level, [True] * 6, today=TODAY)
        second = make_level_ready(self.journey, self.kps[1])
        answer_all(second, [True] * 6, today=TODAY + timedelta(days=3))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.streak, 1)  # 断连重计

    def test_journey_cleared_when_all_levels_cleared(self):
        # 常规关全清 → 旅程未通关 (boss 关未清); boss 清 → 旅程通关
        second = make_level_ready(self.journey, self.kps[1])
        boss = make_level_ready(
            self.journey,
            self.kps[0],
            level_type=CharplotLevel.LevelType.BOSS,
        )
        answer_all(self.level, [True] * 6)
        answer_all(second, [True] * 6)
        self.journey.refresh_from_db()
        self.assertFalse(self.journey.cleared)
        answer_all(boss, [True] * 6)
        self.journey.refresh_from_db()
        self.assertTrue(self.journey.cleared)

    def test_level_clear_event_recorded(self):
        answer_all(self.level, [True] * 6)
        event = CharplotUserEvent.objects.get(
            user=self.user, event_type=CharplotUserEvent.EventType.LEVEL_CLEAR
        )
        self.assertEqual(event.payload["level_id"], self.level.id)

    def test_last_question_wrong_uses_last_heart_fails(self):
        # 前 5 题全错扣完心 → 第 5 题提交后 failed (第 6 题无法作答)
        results = answer_all(self.level, [False] * 5)
        last = results[-1]
        self.assertEqual(last["hearts"], 0)
        self.assertEqual(last["level_status"], "failed")
        self.assertFalse(last["cleared"])
        self.level.refresh_from_db()
        self.assertFalse(self.level.cleared)
        self.assertEqual(level_status(self.level), "failed")
        # 心扣完不可再答, 需重开
        with self.assertRaises(LevelFailedError):
            submit_answer(self.level, self.current().id, self.current().answer)


class ResumeAndRestartTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.profile = self.user.charplot_profile
        self.journey, _, _ = make_journey(self.user)
        self.level = make_level_ready(self.journey)

    def current(self):
        return self.level.questions.order_by("order", "id")[self.level.current_index]

    def test_resume_from_checkpoint(self):
        # 答 2 题 (1 对 1 错) 后退出 → 进度/心保留, 从断点续答
        submit_answer(self.level, self.current().id, self.current().answer)
        submit_answer(self.level, self.current().id, answers_for(self.level, False))
        saved_id, saved_index = self.level.id, self.level.current_index
        self.assertEqual(saved_index, 2)
        self.assertEqual(self.level.hearts, MAX_HEARTS - 1)

        resumed = CharplotLevel.objects.get(pk=saved_id)
        self.assertEqual(resumed.current_index, 2)
        self.assertEqual(resumed.hearts, MAX_HEARTS - 1)
        # 续答的是第 3 题
        third = resumed.questions.order_by("order", "id")[2]
        self.assertEqual(third.id, self.current().id)

    def test_restart_resets_hearts_and_progress_keeps_attempts(self):
        submit_answer(self.level, self.current().id, answers_for(self.level, False))
        submit_answer(self.level, self.current().id, answers_for(self.level, False))
        attempts_before = CharplotAttempt.objects.filter(level=self.level).count()
        self.assertEqual(self.level.hearts, MAX_HEARTS - 2)

        restart_level(self.level)
        self.level.refresh_from_db()
        self.assertEqual(self.level.hearts, MAX_HEARTS)
        self.assertEqual(self.level.current_index, 0)
        self.assertFalse(self.level.cleared)
        # Attempt 历史保留, 不覆盖
        self.assertEqual(
            CharplotAttempt.objects.filter(level=self.level).count(), attempts_before
        )
        # profile.hearts 同步回满
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.hearts, MAX_HEARTS)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class QuizApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.client.force_login(self.user)
        self.journey, _, self.kps = make_journey(self.user)
        self.level = make_level_ready(self.journey)
        self.level_url = f"/api/charplot/levels/{self.level.id}"
        self.list_url = f"/api/charplot/journeys/{self.journey.id}/levels/"

    def current(self):
        return self.level.questions.order_by("order", "id")[self.level.current_index]

    def test_level_list_lazily_creates(self):
        other_journey, _, _ = make_journey(self.user, kp_count=1)
        resp = self.client.get(f"/api/charplot/journeys/{other_journey.id}/levels/")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        # 1 kp 常规关 + 1 章末 boss 关; 题目渐进生成 (pending, 0 题)
        self.assertEqual(len(payload["levels"]), 2)
        item = payload["levels"][0]
        self.assertEqual(item["kp_title"], "知识点1")
        self.assertEqual(item["question_count"], 0)
        self.assertEqual(item["questions_status"], "pending")
        self.assertEqual(item["hearts"], MAX_HEARTS)
        self.assertEqual(item["status"], "pending")
        boss = payload["levels"][1]
        self.assertEqual(boss["level_type"], "boss")
        self.assertTrue(boss["locked"])  # 章内常规关未通关 → boss 锁定

    def test_level_detail_returns_current_question_without_answer(self):
        resp = self.client.get(f"{self.level_url}/")
        payload = resp.json()
        self.assertEqual(payload["status"], "pending")
        question = payload["question"]
        self.assertIsNotNone(question)
        self.assertNotIn("answer", question)
        self.assertIn("options", question)

    def test_level_detail_resume_position(self):
        self.client.post(
            f"{self.level_url}/answer/",
            {"question_id": self.current().id, "answer": self.current().answer},
            format="json",
        )
        resp = self.client.get(f"{self.level_url}/")
        payload = resp.json()
        self.assertEqual(payload["current_index"], 1)
        self.assertEqual(payload["status"], "in_progress")

    def test_answer_correct_flow(self):
        resp = self.client.post(
            f"{self.level_url}/answer/",
            {"question_id": self.current().id, "answer": self.current().answer},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["correct"])
        self.assertEqual(data["hearts"], MAX_HEARTS)
        self.assertIn("explanation", data)
        self.assertIn("sources", data)

    def test_answer_wrong_deducts_heart(self):
        wrong = answers_for(self.level, correct=False)
        resp = self.client.post(
            f"{self.level_url}/answer/",
            {"question_id": self.current().id, "answer": wrong},
            format="json",
        )
        data = resp.json()
        self.assertFalse(data["correct"])
        self.assertEqual(data["hearts"], MAX_HEARTS - 1)

    def test_answer_replay_rejected_400(self):
        question = self.current()
        self.client.post(
            f"{self.level_url}/answer/",
            {"question_id": question.id, "answer": question.answer},
            format="json",
        )
        resp = self.client.post(
            f"{self.level_url}/answer/",
            {"question_id": question.id, "answer": question.answer},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("detail", resp.json())

    def test_answer_invalid_body_400(self):
        resp = self.client.post(f"{self.level_url}/answer/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_full_clear_returns_reward(self):
        for _ in range(6):
            # 服务层推进进度后刷新内存对象, 保证取到当前题
            self.level.refresh_from_db()
            resp = self.client.post(
                f"{self.level_url}/answer/",
                {"question_id": self.current().id, "answer": self.current().answer},
                format="json",
            )
            self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["cleared"])
        self.assertEqual(data["reward"]["coins"], LEVEL_CLEAR_COINS)
        self.assertIn("xp", data["reward"])
        self.assertIn("streak", data["reward"])

    def test_restart_after_failed(self):
        for _ in range(5):
            self.level.refresh_from_db()
            wrong = answers_for(self.level, correct=False)
            resp = self.client.post(
                f"{self.level_url}/answer/",
                {"question_id": self.current().id, "answer": wrong},
                format="json",
            )
        self.assertEqual(resp.json()["level_status"], "failed")
        resp = self.client.post(f"{self.level_url}/restart/")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["hearts"], MAX_HEARTS)
        self.assertEqual(payload["current_index"], 0)
        self.assertEqual(payload["status"], "pending")

    def test_restart_cleared_level_rejected(self):
        for _ in range(6):
            self.level.refresh_from_db()
            self.client.post(
                f"{self.level_url}/answer/",
                {"question_id": self.current().id, "answer": self.current().answer},
                format="json",
            )
        resp = self.client.post(f"{self.level_url}/restart/")
        self.assertEqual(resp.status_code, 400)

    def test_level_of_other_user_404(self):
        other = create_user("bob")
        other_journey, _, _ = make_journey(other)
        ensure_levels_for_journey(other_journey)
        other_level = other_journey.levels.first()
        resp = self.client.get(f"/api/charplot/levels/{other_level.id}/")
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post(
            f"/api/charplot/levels/{other_level.id}/answer/",
            {"question_id": 1, "answer": [0]},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_forbidden(self):
        anon = APIClient()
        resp = anon.get(self.list_url)
        self.assertEqual(resp.status_code, 403)
        resp = anon.post(f"{self.level_url}/answer/")
        self.assertEqual(resp.status_code, 403)
