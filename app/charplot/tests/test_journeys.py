"""旅程链路测试 (Issue 03).

覆盖创建 (text/link/file 三形态) / 列表 / 详情 / 内部落库端点 (token 校验 +
契约校验 + 幂等) / 失败标记 / 服务层契约校验.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from app.charplot.models import (
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
)
from app.charplot.services import (
    JOURNEY_GRAPH_VERSION,
    JourneyGraphError,
    validate_graph,
)

User = get_user_model()

JOURNEYS_URL = "/api/charplot/journeys/"
INTERNAL_TOKEN = "test-internal-token"

# 契约合法图谱: 2 章节 + 跨章节依赖边 (CONTRACT.md v1)
VALID_GRAPH = {
    "version": JOURNEY_GRAPH_VERSION,
    "title": "Python 装饰器",
    "chapters": [
        {
            "id": "ch_1",
            "title": "函数基础",
            "summary": "装饰器的前提知识",
            "knowledge_points": [
                {
                    "id": "kp_1",
                    "title": "函数是一等公民",
                    "summary": "可传递可返回",
                    "prerequisites": [],
                },
                {
                    "id": "kp_2",
                    "title": "闭包",
                    "summary": "词法作用域",
                    "prerequisites": ["kp_1"],
                },
            ],
        },
        {
            "id": "ch_2",
            "title": "装饰器实践",
            "summary": "语法与实战",
            "knowledge_points": [
                {
                    "id": "kp_3",
                    "title": "装饰器语法糖",
                    "summary": "@ 语法",
                    "prerequisites": ["kp_2"],
                },
                {
                    "id": "kp_4",
                    "title": "带参数装饰器",
                    "summary": "三层嵌套",
                    "prerequisites": ["kp_3"],
                },
            ],
        },
    ],
}


def create_user(username="alice", password="TestPass#2026"):
    return User.objects.create_user(username=username, password=password)


class JourneyCreateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.client.force_login(self.user)

    def test_create_text_journey(self):
        resp = self.client.post(
            JOURNEYS_URL,
            {"input_type": "text", "content": "我想学 Python 装饰器"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["status"], "generating")
        journey = CharplotJourney.objects.get(pk=resp.json()["journey_id"])
        self.assertEqual(journey.user, self.user)
        self.assertEqual(journey.input_type, "text")
        self.assertEqual(journey.title, "我想学 Python 装饰器")

    def test_create_link_journey(self):
        resp = self.client.post(
            JOURNEYS_URL,
            {"input_type": "link", "content": "https://docs.python.org/3/"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_create_file_journey_multipart(self):
        upload = SimpleUploadedFile("intro.txt", b"hello", content_type="text/plain")
        resp = self.client.post(
            JOURNEYS_URL, {"input_type": "file", "source_file": upload}
        )
        self.assertEqual(resp.status_code, 201)
        journey = CharplotJourney.objects.get(pk=resp.json()["journey_id"])
        self.assertEqual(journey.input_type, "file")
        self.assertEqual(journey.title, "intro")
        self.assertIsNotNone(journey.source_file)
        # 文件落盘到 uploads 目录 (MEDIA_ROOT 下相对路径)
        self.assertTrue(journey.source_file.name.startswith("app/charplot/uploads/"))
        self.assertTrue(journey.source_file.storage.exists(journey.source_file.name))

    def test_create_text_missing_content_rejected(self):
        resp = self.client.post(JOURNEYS_URL, {"input_type": "text"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn(
            "文本/链接输入必须提供 content",
            resp.json().get("non_field_errors", []),
        )

    def test_create_invalid_link_rejected(self):
        resp = self.client.post(
            JOURNEYS_URL, {"input_type": "link", "content": "not-a-url"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_file_missing_file_rejected(self):
        resp = self.client.post(JOURNEYS_URL, {"input_type": "file"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_create_anonymous_forbidden(self):
        anon = APIClient()
        resp = anon.post(
            JOURNEYS_URL, {"input_type": "text", "content": "x"}, format="json"
        )
        self.assertEqual(resp.status_code, 403)


class JourneyListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.client.force_login(self.user)
        self.journey = CharplotJourney.objects.create(
            user=self.user, title="我的旅程", input_type="text", content="学习内容"
        )
        # 预置落库数据验证计数
        chapter = CharplotChapter.objects.create(
            journey=self.journey, title="章节", order=0
        )
        CharplotKnowledgePoint.objects.create(chapter=chapter, title="知识点", order=0)

    def test_list_returns_only_own_journeys(self):
        other = create_user("bob")
        CharplotJourney.objects.create(
            user=other, title="他人旅程", input_type="text", content="x"
        )
        resp = self.client.get(JOURNEYS_URL)
        self.assertEqual(resp.status_code, 200)
        journeys = resp.json()["journeys"]
        self.assertEqual(len(journeys), 1)
        self.assertEqual(journeys[0]["title"], "我的旅程")
        self.assertEqual(journeys[0]["chapter_count"], 1)
        self.assertEqual(journeys[0]["kp_count"], 1)
        self.assertFalse(journeys[0]["cleared"])

    def test_list_ordered_by_created_desc(self):
        CharplotJourney.objects.create(
            user=self.user, title="更新旅程", input_type="text", content="x"
        )
        resp = self.client.get(JOURNEYS_URL)
        titles = [j["title"] for j in resp.json()["journeys"]]
        self.assertEqual(titles, ["更新旅程", "我的旅程"])


class JourneyDetailTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.client.force_login(self.user)
        self.journey = CharplotJourney.objects.create(
            user=self.user, title="我的旅程", input_type="text", content="学习内容"
        )
        self.chapter = CharplotChapter.objects.create(
            journey=self.journey, title="章节", summary="概述", order=0
        )
        self.kp1 = CharplotKnowledgePoint.objects.create(
            chapter=self.chapter, title="前置知识点", order=0
        )
        self.kp2 = CharplotKnowledgePoint.objects.create(
            chapter=self.chapter, title="知识点", order=1
        )
        self.kp2.prerequisites.add(self.kp1)

    def test_detail_returns_nested_graph_with_db_primary_keys(self):
        resp = self.client.get(f"{JOURNEYS_URL}{self.journey.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["title"], "我的旅程")
        self.assertEqual(data["status"], "generating")
        self.assertEqual(len(data["chapters"]), 1)
        chapter = data["chapters"][0]
        self.assertEqual(chapter["id"], self.chapter.id)
        kps = chapter["knowledge_points"]
        self.assertEqual(len(kps), 2)
        # prerequisites 返回 DB 主键 int 列表
        self.assertEqual(kps[1]["prerequisites"], [self.kp1.id])
        # graph 快照不返回
        self.assertNotIn("graph", data)

    def test_detail_other_users_journey_404(self):
        other = create_user("bob")
        other_journey = CharplotJourney.objects.create(
            user=other, title="他人旅程", input_type="text", content="x"
        )
        resp = self.client.get(f"{JOURNEYS_URL}{other_journey.id}/")
        self.assertEqual(resp.status_code, 404)


@override_settings(CHARPLOT_INTERNAL_TOKEN=INTERNAL_TOKEN)
class JourneyGraphInternalTests(TestCase):
    """内部落库端点: token 校验 + 契约校验 + 幂等 (FastAPI → Django)."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.journey = CharplotJourney.objects.create(
            user=self.user, title="我的旅程", input_type="text", content="学习内容"
        )
        self.graph_url = f"{JOURNEYS_URL}{self.journey.id}/graph/"
        self.status_url = f"{JOURNEYS_URL}{self.journey.id}/status/"

    def test_save_graph_with_token(self):
        resp = self.client.post(
            self.graph_url,
            {"task_id": "task-1", "graph": VALID_GRAPH},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")

        journey = CharplotJourney.objects.get(pk=self.journey.id)
        self.assertEqual(journey.status, "ready")
        self.assertEqual(journey.latest_task_id, "task-1")
        self.assertEqual(journey.graph["version"], JOURNEY_GRAPH_VERSION)
        # 章节/知识点落库 + 跨章节依赖边接好
        self.assertEqual(journey.chapters.count(), 2)
        kp3 = CharplotKnowledgePoint.objects.get(
            chapter__journey=journey, title="装饰器语法糖"
        )
        kp2 = CharplotKnowledgePoint.objects.get(chapter__journey=journey, title="闭包")
        self.assertEqual(list(kp3.prerequisites.all()), [kp2])

    def test_save_graph_without_token_forbidden(self):
        resp = self.client.post(
            self.graph_url, {"task_id": "t", "graph": VALID_GRAPH}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            CharplotJourney.objects.get(pk=self.journey.id).status, "generating"
        )

    def test_save_graph_wrong_token_forbidden(self):
        resp = self.client.post(
            self.graph_url,
            {"task_id": "t", "graph": VALID_GRAPH},
            format="json",
            HTTP_X_INTERNAL_TOKEN="wrong-token",
        )
        self.assertEqual(resp.status_code, 403)

    def test_save_invalid_graph_rejected_and_rolled_back(self):
        bad_graph = {
            "version": JOURNEY_GRAPH_VERSION,
            "title": "坏图谱",
            "chapters": [
                {
                    "id": "ch_1",
                    "title": "章节",
                    "knowledge_points": [
                        {
                            "id": "kp_1",
                            "title": "知识点",
                            "prerequisites": ["kp_99"],
                        },  # 未知引用
                    ],
                }
            ],
        }
        resp = self.client.post(
            self.graph_url,
            {"task_id": "t", "graph": bad_graph},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        self.assertEqual(resp.status_code, 400)
        # 事务回滚: 无部分行写入
        self.assertEqual(self.journey.chapters.count(), 0)
        self.assertEqual(
            CharplotJourney.objects.get(pk=self.journey.id).status, "generating"
        )

    def test_save_graph_twice_idempotent(self):
        for _ in range(2):
            resp = self.client.post(
                self.graph_url,
                {"task_id": "task-1", "graph": VALID_GRAPH},
                format="json",
                HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
            )
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.journey.chapters.count(), 2)
        self.assertEqual(
            CharplotKnowledgePoint.objects.filter(
                chapter__journey=self.journey
            ).count(),
            4,
        )

    def test_mark_failed_with_token(self):
        resp = self.client.post(
            self.status_url,
            {"task_id": "task-2", "error_message": "落库超时"},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        self.assertEqual(resp.status_code, 200)
        journey = CharplotJourney.objects.get(pk=self.journey.id)
        self.assertEqual(journey.status, "failed")
        self.assertEqual(journey.latest_task_id, "task-2")
        self.assertEqual(journey.error_message, "落库超时")

    def test_mark_failed_wrong_token_forbidden(self):
        resp = self.client.post(
            self.status_url,
            {"task_id": "t", "error_message": "x"},
            format="json",
            HTTP_X_INTERNAL_TOKEN="wrong",
        )
        self.assertEqual(resp.status_code, 403)


class ValidateGraphTests(TestCase):
    """服务层契约校验单测 (CONTRACT.md v1 非法形态)."""

    def test_valid_graph_passes(self):
        validate_graph(VALID_GRAPH)  # 不抛异常即通过

    def test_invalid_structures_rejected(self):
        cases = [
            ("非对象", "not-a-dict"),
            ("版本不符", {**VALID_GRAPH, "version": 999}),
            ("缺 title", {k: v for k, v in VALID_GRAPH.items() if k != "title"}),
            ("空章节", {**VALID_GRAPH, "chapters": []}),
            (
                "章节缺 id",
                {
                    "version": JOURNEY_GRAPH_VERSION,
                    "title": "t",
                    "chapters": [{"title": "c"}],
                },
            ),
            (
                "章节空知识点",
                {
                    "version": JOURNEY_GRAPH_VERSION,
                    "title": "t",
                    "chapters": [{"id": "c1", "title": "c", "knowledge_points": []}],
                },
            ),
        ]
        for name, graph in cases:
            with self.assertRaises(JourneyGraphError, msg=name):
                validate_graph(graph)

    def test_duplicate_tmp_id_rejected(self):
        graph = {
            "version": JOURNEY_GRAPH_VERSION,
            "title": "t",
            "chapters": [
                {
                    "id": "ch_1",
                    "title": "c1",
                    "knowledge_points": [
                        {"id": "kp_1", "title": "a", "prerequisites": []}
                    ],
                },
                {
                    "id": "ch_2",
                    "title": "c2",
                    "knowledge_points": [
                        {"id": "kp_1", "title": "b", "prerequisites": []}
                    ],
                },
            ],
        }
        with self.assertRaises(JourneyGraphError):
            validate_graph(graph)

    def test_unknown_prerequisite_rejected(self):
        graph = {
            "version": JOURNEY_GRAPH_VERSION,
            "title": "t",
            "chapters": [
                {
                    "id": "ch_1",
                    "title": "c",
                    "knowledge_points": [
                        {"id": "kp_1", "title": "a", "prerequisites": ["kp_2"]}
                    ],
                },
            ],
        }
        with self.assertRaises(JourneyGraphError):
            validate_graph(graph)
