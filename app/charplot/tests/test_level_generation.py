"""题目渐进生成 + 间隔复习 + Boss 解锁测试 (Issue 08).

覆盖: 生成任务抢占 (claim 幂等/陈旧超时) / 内部端点认证与落库 (含
update-in-place 保 Attempt) / 间隔复习算法 (易错分与时间衰减排序、  # noqa: RUF002
Top 20% 混入、选题、衰减闭环) / Boss 锁定与解锁 / submit_answer 守卫
(题目未就绪、锁定关、复习题易错分路由).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from app.charplot.models import (
    CharplotAttempt,
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
    CharplotLevel,
    CharplotQuestion,
)
from app.charplot.services import (
    BOSS_QUESTION_COUNT,
    LEVEL_QUESTION_TARGET,
    LevelNotReadyError,
    _review_candidates,
    build_level_generation_input,
    claim_level_generation,
    ensure_levels_for_journey,
    level_locked,
    restart_level,
    save_generated_questions,
    submit_answer,
)
from app.charplot.tests.test_quiz import (
    answers_for,
    create_user,
    make_journey,
    make_level_ready,
)

User = get_user_model()

TODAY = timezone.localdate()
INTERNAL_TOKEN = "test-internal-token"


def make_multi_chapter_journey(user, chapter_kps=(1, 1)):
    """工厂: 多章 journey (每章 N 个线性知识点), 返回 (journey, [(chapter, kps)]) ."""
    journey = CharplotJourney.objects.create(
        user=user, title="多章旅程", input_type="text"
    )
    chapters = []
    for ch_idx, kp_count in enumerate(chapter_kps):
        chapter = CharplotChapter.objects.create(
            journey=journey, title=f"章节{ch_idx + 1}", order=ch_idx
        )
        kps = []
        for i in range(kp_count):
            kp = CharplotKnowledgePoint.objects.create(
                chapter=chapter,
                title=f"章节{ch_idx + 1}知识点{i + 1}",
                summary="核心知识点概述",
                order=i,
            )
            kps.append(kp)
        chapters.append((chapter, kps))
    return journey, chapters


def valid_question(qtype="choice", order=0):
    """合法题目 dict (与 FastAPI 落库载荷同构)."""
    if qtype == "choice":
        return {
            "question_type": "choice",
            "content": f"合法选择题 {order}",
            "options": ["正确", "干扰A", "干扰B", "干扰C"],
            "answer": [0],
            "explanation": "讲解",
            "sources": [],
        }
    if qtype == "judge":
        return {
            "question_type": "judge",
            "content": f"合法判断题 {order}",
            "options": [],
            "answer": ["true"],
            "explanation": "讲解",
            "sources": [],
        }
    return {
        "question_type": "fill",
        "content": f"合法填空题 {order}: ____",
        "options": [],
        "answer": ["标准答案"],
        "explanation": "讲解",
        "sources": [],
    }


def current_question(level):
    return level.questions.order_by("order", "id")[level.current_index]


# ---------------------------------------------------------------------------
# 生成任务抢占 (claim)
# ---------------------------------------------------------------------------


@override_settings(CHARPLOT_INTERNAL_TOKEN=INTERNAL_TOKEN)
class ClaimGenerationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.journey, _, self.kps = make_journey(self.user)
        self.level = ensure_levels_for_journey(self.journey)[0]
        self.url = f"/api/charplot/journeys/{self.journey.id}/level-generation/"

    def claim(self, task_id="task-1", seq=None):
        return self.client.post(
            self.url,
            {"task_id": task_id, "level_seq": seq or self.level.seq},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )

    def test_claim_marks_generating_and_returns_input(self):
        resp = self.claim()
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["claimed"])
        input_data = payload["input"]
        self.assertEqual(input_data["level_seq"], self.level.seq)
        self.assertEqual(input_data["level_type"], "regular")
        self.assertEqual(input_data["difficulty"], "medium")
        self.assertEqual(input_data["question_count"], LEVEL_QUESTION_TARGET)
        self.assertEqual(input_data["new_count"], LEVEL_QUESTION_TARGET)
        self.assertEqual(input_data["kp"]["id"], self.kps[0].id)
        # 抢占据于服务端: 状态已置 generating
        self.level.refresh_from_db()
        self.assertEqual(
            self.level.questions_status, CharplotLevel.QuestionsStatus.GENERATING
        )
        self.assertEqual(self.level.latest_task_id, "task-1")

    def test_second_claim_rejected_while_generating(self):
        self.claim()
        resp = self.claim(task_id="task-2")
        payload = resp.json()
        self.assertFalse(payload["claimed"])
        self.assertEqual(payload["reason"], "generating")
        self.assertEqual(payload["task_id"], "task-1")

    def test_ready_level_claim_rejected(self):
        ready = make_level_ready(self.journey)
        resp = self.client.post(
            self.url,
            {"task_id": "task-9", "level_seq": ready.seq},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        payload = resp.json()
        self.assertFalse(payload["claimed"])
        self.assertEqual(payload["reason"], "ready")

    def test_stale_generating_can_reclaim(self):
        # FastAPI 崩溃后任务丢失: generating 状态超过陈旧阈值 → 可重新抢占
        self.claim()
        CharplotLevel.objects.filter(pk=self.level.pk).update(
            updated_at=timezone.now() - timedelta(minutes=11)
        )
        claimed, _ = claim_level_generation(self.level, "task-2")
        self.assertTrue(claimed)
        self.level.refresh_from_db()
        self.assertEqual(self.level.latest_task_id, "task-2")

    def test_unknown_seq_404(self):
        resp = self.client.post(
            self.url,
            {"task_id": "task-x", "level_seq": 999},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        self.assertEqual(resp.status_code, 404)

    def test_missing_token_403(self):
        resp = self.client.post(
            self.url, {"task_id": "t", "level_seq": 1}, format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_wrong_token_403(self):
        resp = self.client.post(
            self.url,
            {"task_id": "t", "level_seq": 1},
            format="json",
            HTTP_X_INTERNAL_TOKEN="wrong-token",
        )
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# 题目落库 / 失败标记
# ---------------------------------------------------------------------------


@override_settings(CHARPLOT_INTERNAL_TOKEN=INTERNAL_TOKEN)
class SaveQuestionsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.journey, _, self.kps = make_journey(self.user)
        self.level = ensure_levels_for_journey(self.journey)[0]
        self.claim_url = f"/api/charplot/journeys/{self.journey.id}/level-generation/"
        self.save_url = (
            f"/api/charplot/journeys/{self.journey.id}/level-generation/questions/"
        )

    def save(self, questions, task_id="task-1", seq=None):
        return self.client.post(
            self.save_url,
            {
                "task_id": task_id,
                "level_seq": seq or self.level.seq,
                "questions": questions,
            },
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )

    def test_save_valid_questions_marks_ready(self):
        questions = [valid_question("choice", 0), valid_question("judge", 1)]
        resp = self.save(questions)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")
        self.level.refresh_from_db()
        self.assertEqual(
            self.level.questions_status, CharplotLevel.QuestionsStatus.READY
        )
        self.assertEqual(self.level.latest_task_id, "task-1")
        self.assertEqual(self.level.questions.count(), 2)
        first = self.level.questions.order_by("order", "id").first()
        self.assertEqual(first.content, "合法选择题 0")
        self.assertEqual(first.order, 0)

    def test_save_unknown_type_400(self):
        questions = [{"question_type": "essay", "content": "题", "answer": []}]
        resp = self.save(questions)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("detail", resp.json())
        self.level.refresh_from_db()
        # 校验失败不改变生成状态 (未 claim 前保持 pending)
        self.assertEqual(
            self.level.questions_status, CharplotLevel.QuestionsStatus.PENDING
        )

    def test_save_invalid_choice_answer_400(self):
        questions = [
            {
                "question_type": "choice",
                "content": "选择题",
                "options": ["A", "B", "C"],
                "answer": [5],  # 下标越界
                "explanation": "讲解",
            }
        ]
        resp = self.save(questions)
        self.assertEqual(resp.status_code, 400)

    def test_save_update_in_place_keeps_attempts(self):
        # 有 Attempt 的关卡重生成: 复用旧题 id 原地更新, 答题历史不丢
        level = make_level_ready(self.journey)
        submit_answer(level, current_question(level).id, current_question(level).answer)
        submit_answer(level, current_question(level).id, answers_for(level, False))
        attempts_before = CharplotAttempt.objects.filter(level=level).count()
        old_ids = list(level.questions.values_list("id", flat=True))

        questions = [
            valid_question("choice", 0),
            valid_question("judge", 1),
            valid_question("fill", 2),
        ]
        resp = self.save(questions, seq=level.seq)
        self.assertEqual(resp.status_code, 200)
        level.refresh_from_db()
        # 题目被原地更新 (id 复用), Attempt 行数不变
        self.assertEqual(
            list(level.questions.values_list("id", flat=True)), old_ids[:3]
        )
        self.assertEqual(
            CharplotAttempt.objects.filter(level=level).count(), attempts_before
        )
        first = level.questions.order_by("order", "id").first()
        self.assertEqual(first.content, "合法选择题 0")
        self.assertEqual(first.question_type, CharplotQuestion.QuestionType.CHOICE)

    def test_save_no_attempts_rebuilds(self):
        questions = [valid_question("choice", 0)]
        resp = self.save(questions)
        self.assertEqual(resp.status_code, 200)
        self.level.refresh_from_db()
        self.assertEqual(self.level.questions.count(), 1)


@override_settings(CHARPLOT_INTERNAL_TOKEN=INTERNAL_TOKEN)
class FailedMarkTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.journey, _, _ = make_journey(self.user)
        self.level = ensure_levels_for_journey(self.journey)[0]
        self.url = f"/api/charplot/journeys/{self.journey.id}/level-generation/failed/"

    def test_failed_marks_level(self):
        resp = self.client.post(
            self.url,
            {
                "task_id": "task-1",
                "level_seq": self.level.seq,
                "error_message": "LLM 超时",
            },
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        self.assertEqual(resp.status_code, 200)
        self.level.refresh_from_db()
        self.assertEqual(
            self.level.questions_status, CharplotLevel.QuestionsStatus.FAILED
        )
        self.assertEqual(self.level.latest_task_id, "task-1")

    def test_missing_token_403(self):
        resp = self.client.post(
            self.url, {"task_id": "t", "level_seq": 1}, format="json"
        )
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# 间隔复习算法 (易错分 × 时间衰减 Top 20%)  # noqa: RUF003
# ---------------------------------------------------------------------------


class ReviewAlgorithmTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, _, self.kps = make_journey(self.user)

    def test_decay_prefers_never_reviewed(self):
        # kp1: 易错分 2, 昨天复习过 → priority = 2 * (1+1) = 4
        # kp2: 易错分 1, 从未复习 (30 天计) → priority = 1 * 31 = 31
        # 时间衰减使 kp2 排前 (若不衰减则 kp1 2 分在前)
        kp1, kp2 = self.kps
        level2 = make_level_ready(self.journey, kp2)
        submit_answer(level2, current_question(level2).id, answers_for(level2, False))
        kp1.error_score = 2
        kp1.last_reviewed_at = timezone.now() - timedelta(days=1)
        CharplotKnowledgePoint.objects.bulk_update(
            [kp1], ["error_score", "last_reviewed_at"]
        )
        kp3 = CharplotKnowledgePoint.objects.create(
            chapter=kp1.chapter, title="知识点3", summary="概述", order=2
        )
        questions = build_level_generation_input(
            make_level_ready(self.journey, kp3), today=TODAY
        )["review_questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["source_kp_id"], kp2.id)

    def test_never_reviewed_uses_30_days_cap(self):
        kp = self.kps[1]
        kp.error_score = 1
        kp.save(update_fields=["error_score"])
        self.assertEqual(
            _review_candidates(self.journey, {self.kps[0].id}, TODAY),
            [kp],
        )

    def test_no_candidates_when_error_score_zero(self):
        questions = build_level_generation_input(
            make_level_ready(self.journey, self.kps[0]), today=TODAY
        )["review_questions"]
        self.assertEqual(questions, [])
        self.assertEqual(
            build_level_generation_input(
                make_level_ready(self.journey, self.kps[0]), today=TODAY
            )["new_count"],
            LEVEL_QUESTION_TARGET,
        )

    def test_review_count_bounded_by_desired_and_top_k(self):
        kp1, kp2 = self.kps
        level2 = make_level_ready(self.journey, kp2)
        submit_answer(level2, current_question(level2).id, answers_for(level2, False))
        # len(C)=1 (kp1 被新关排除): top_k=ceil(0.2*1)=1, desired=1 → 1 题
        questions = build_level_generation_input(
            make_level_ready(self.journey, kp1), today=TODAY
        )["review_questions"]
        self.assertEqual(len(questions), 1)

    def test_review_picks_most_wrong_question(self):
        # kp1 有两关各一题, 一题答错 2 次另一题答错 1 次 → 复习选错得多的
        kp = self.kps[1]
        level_a = make_level_ready(self.journey, kp)
        level_b = make_level_ready(self.journey, kp)
        # level_a 第 1 题 (choice) 答错 2 次 (答错 → 重开 → 再答错)
        question_a = current_question(level_a)
        submit_answer(level_a, question_a.id, answers_for(level_a, False))
        restart_level(level_a)
        submit_answer(level_a, question_a.id, answers_for(level_a, False))
        # level_b 第 1 题答错 1 次
        submit_answer(
            level_b, current_question(level_b).id, answers_for(level_b, False)
        )
        kp.refresh_from_db()
        self.assertEqual(kp.error_score, 6)  # 3 次答错 × 2  # noqa: RUF003

        # 新关 (kp1) 生成 → 复习题应选 level_a 的题 (错 2 次)
        questions = build_level_generation_input(
            make_level_ready(self.journey, self.kps[0]), today=TODAY
        )["review_questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["source_kp_id"], kp.id)
        self.assertEqual(questions[0]["content"], question_a.content)

    def test_boss_excludes_chapter_kps(self):
        journey, chapters = make_multi_chapter_journey(self.user, (2, 1))
        ch1_kps = chapters[0][1]
        # 第一章两个 kp 都易错
        for kp in ch1_kps:
            kp.error_score = 2
            kp.save(update_fields=["error_score"])
        # 第一章 boss 关: 排除本章全部 kp → 无复习候选 (第二章 kp 无易错分)
        boss = make_level_ready(
            journey, ch1_kps[0], level_type=CharplotLevel.LevelType.BOSS
        )
        questions = build_level_generation_input(boss, today=TODAY)["review_questions"]
        self.assertEqual(questions, [])

    def test_review_appended_at_end_on_save(self):
        # 复习题保存后置于末尾, 来源 kp 的 last_reviewed_at 更新 (衰减闭环)
        kp1, kp2 = self.kps
        level1 = make_level_ready(self.journey, kp1)
        submit_answer(level1, current_question(level1).id, answers_for(level1, False))
        kp1.refresh_from_db()
        self.assertEqual(kp1.error_score, 2)

        level2 = CharplotLevel.objects.create(
            journey=self.journey,
            knowledge_point=kp2,
            chapter=kp2.chapter,
            seq=9,
            questions_status=CharplotLevel.QuestionsStatus.GENERATING,
        )
        input_data = build_level_generation_input(level2, today=TODAY)
        self.assertEqual(len(input_data["review_questions"]), 1)
        new_count = input_data["new_count"]
        final = [valid_question("choice", i) for i in range(new_count)]
        final += input_data["review_questions"]
        save_generated_questions(level2, "task-1", final)

        level2.refresh_from_db()
        self.assertEqual(level2.questions.count(), LEVEL_QUESTION_TARGET)
        questions = list(level2.questions.order_by("order", "id"))
        self.assertEqual(
            questions[-1].source_kp_id,
            kp1.id,  # 复习题在末尾, 带来源锚点
        )
        self.assertIsNone(questions[0].source_kp_id)  # 新题无来源
        kp1.refresh_from_db()
        self.assertIsNotNone(kp1.last_reviewed_at)

    def test_boss_question_count_8(self):
        journey, chapters = make_multi_chapter_journey(self.user, (1, 1))
        boss = make_level_ready(
            journey, chapters[0][1][0], level_type=CharplotLevel.LevelType.BOSS
        )
        input_data = build_level_generation_input(boss, today=TODAY)
        self.assertEqual(input_data["question_count"], BOSS_QUESTION_COUNT)
        self.assertEqual(input_data["difficulty"], "high")
        self.assertEqual(len(input_data["kp_infos"]), 1)  # 本章全部 kp


# ---------------------------------------------------------------------------
# Boss 锁定与解锁 (G-5)
# ---------------------------------------------------------------------------


class BossLockTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, self.chapters = make_multi_chapter_journey(self.user, (1, 1))
        self.ch1, self.kps1 = self.chapters[0]
        self.ch2, self.kps2 = self.chapters[1]
        self.l1 = make_level_ready(self.journey, self.kps1[0])
        self.boss1 = make_level_ready(
            self.journey, self.kps1[0], level_type=CharplotLevel.LevelType.BOSS
        )
        self.l2 = make_level_ready(self.journey, self.kps2[0])

    def test_lock_rules(self):
        self.assertFalse(level_locked(self.l1))  # 第一章常规关永不锁
        self.assertTrue(level_locked(self.boss1))  # 章内常规关未清 → boss 锁
        self.assertTrue(level_locked(self.l2))  # 前置章 boss 未清 → 下一章锁

    def test_unlock_after_clearing_boss(self):
        from app.charplot.tests.test_quiz import answer_all

        answer_all(self.l1, [True] * 6)
        self.assertFalse(level_locked(self.boss1))  # 常规关全清 → boss 解锁
        self.assertTrue(level_locked(self.l2))
        answer_all(self.boss1, [True] * 6)
        self.assertFalse(level_locked(self.l2))  # boss 通关 → 下一章解锁

    def test_answer_locked_level_rejected_400(self):
        # 第二章常规关在第一章 boss 未通关时锁定 → 答题 400 (后端强制)
        client = APIClient()
        client.force_login(self.user)
        resp = client.post(
            f"/api/charplot/levels/{self.l2.id}/answer/",
            {"question_id": current_question(self.l2).id, "answer": [0]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Boss", resp.json()["detail"])


# ---------------------------------------------------------------------------
# submit_answer 守卫 (Issue 08)
# ---------------------------------------------------------------------------


class SubmitGuardTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey, _, self.kps = make_journey(self.user)

    def test_answer_not_ready_rejected(self):
        level = ensure_levels_for_journey(self.journey)[0]
        with self.assertRaises(LevelNotReadyError):
            submit_answer(level, 1, [0])

    def test_review_wrong_updates_source_kp(self):
        # 复习题答错 → 易错分 +2 记在来源知识点, 本关 kp 不动
        kp1, kp2 = self.kps
        level2 = CharplotLevel.objects.create(
            journey=self.journey,
            knowledge_point=kp2,
            chapter=kp2.chapter,
            seq=2,
            questions_status=CharplotLevel.QuestionsStatus.READY,
        )
        review_question = CharplotQuestion.objects.create(
            level=level2,
            question_type=CharplotQuestion.QuestionType.JUDGE,
            content="历史复习题",
            options=[],
            answer=["true"],
            explanation="讲解",
            source_kp=kp1,
            order=0,
        )
        submit_answer(level2, review_question.id, ["false"])
        kp1.refresh_from_db()
        kp2.refresh_from_db()
        self.assertEqual(kp1.error_score, 2)  # 来源 kp +2
        self.assertEqual(kp2.error_score, 0)  # 本关 kp 不动

    def test_review_correct_decrements_source_kp(self):
        kp1, kp2 = self.kps
        kp1.error_score = 3
        kp1.save(update_fields=["error_score"])
        level2 = CharplotLevel.objects.create(
            journey=self.journey,
            knowledge_point=kp2,
            chapter=kp2.chapter,
            seq=2,
            questions_status=CharplotLevel.QuestionsStatus.READY,
        )
        review_question = CharplotQuestion.objects.create(
            level=level2,
            question_type=CharplotQuestion.QuestionType.JUDGE,
            content="历史复习题",
            options=[],
            answer=["true"],
            explanation="讲解",
            source_kp=kp1,
            order=0,
        )
        submit_answer(level2, review_question.id, ["true"])
        kp1.refresh_from_db()
        self.assertEqual(kp1.error_score, 2)  # 答对 -1
