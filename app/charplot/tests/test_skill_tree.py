"""技能树地图测试 (Issue 04).

覆盖服务层状态计算 (无前置解锁 / 依赖锁定 / 已通关点亮) 与 skill-tree
接口 (payload 结构 / 权限隔离). 关卡进度合并字段 (cleared_levels /
total_levels) 本期无 Level 数据恒为 0, Issue 05 流入.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from app.charplot.models import (
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
)
from app.charplot.services import _kp_status, build_skill_tree

User = get_user_model()

SKILL_TREE_URL = "/api/charplot/journeys/{id}/skill-tree/"


def create_user(username="alice", password="TestPass#2026"):
    return User.objects.create_user(username=username, password=password)


class KpStatusTests(TestCase):
    """点亮状态纯函数单测: locked/unlocked 判定 (Issue 05 后 cleared 由调用侧注入)."""

    def test_no_prerequisites_unlocked(self):
        self.assertEqual(_kp_status([], set()), "unlocked")

    def test_prerequisites_not_cleared_locked(self):
        self.assertEqual(_kp_status([1], set()), "locked")

    def test_prerequisites_partially_cleared_locked(self):
        self.assertEqual(_kp_status([1, 2], {1}), "locked")

    def test_all_prerequisites_cleared_unlocked(self):
        self.assertEqual(_kp_status([1, 2], {1, 2}), "unlocked")

    def test_cross_chapter_prerequisite_locked(self):
        # 前置可来自其他章节, 状态判定不区分章节
        self.assertEqual(_kp_status([99], {98}), "locked")


class BuildSkillTreeTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.journey = CharplotJourney.objects.create(
            user=self.user, title="装饰器旅程", input_type="text"
        )
        self.chapter = CharplotChapter.objects.create(
            journey=self.journey, title="函数基础", order=0
        )
        self.kp1 = CharplotKnowledgePoint.objects.create(
            chapter=self.chapter, title="函数是一等公民", order=0
        )
        self.kp2 = CharplotKnowledgePoint.objects.create(
            chapter=self.chapter, title="闭包", order=1
        )
        self.kp2.prerequisites.add(self.kp1)

    def nodes_by_id(self, data):
        return {n["id"]: n for n in data["nodes"]}

    def test_nodes_and_edges_structure(self):
        data = build_skill_tree(self.journey)
        self.assertEqual(len(data["nodes"]), 2)
        self.assertEqual(
            data["edges"],
            [
                {
                    "id": f"e-{self.kp1.id}-{self.kp2.id}",
                    "source": self.kp1.id,
                    "target": self.kp2.id,
                }
            ],
        )
        # 节点含章节归属与进度合并字段 (前端渲染地图与徽章)
        node = data["nodes"][0]
        self.assertEqual(node["chapter_id"], self.chapter.id)
        self.assertEqual(node["chapter_title"], "函数基础")
        self.assertEqual(node["cleared_levels"], 0)
        self.assertEqual(node["total_levels"], 0)

    def test_lock_state_without_progress(self):
        # 本期无通关数据: 无前置 → unlocked, 有前置 → locked
        by_id = self.nodes_by_id(build_skill_tree(self.journey))
        self.assertEqual(by_id[self.kp1.id]["status"], "unlocked")
        self.assertEqual(by_id[self.kp2.id]["status"], "locked")

    def test_cleared_and_dependency_unlock_after_progress(self):
        # Issue 05 传入已通关集合: 已通关点亮 + 依赖满足解锁
        by_id = self.nodes_by_id(build_skill_tree(self.journey, {self.kp1.id}))
        self.assertEqual(by_id[self.kp1.id]["status"], "cleared")
        self.assertEqual(by_id[self.kp2.id]["status"], "unlocked")

    def test_empty_journey_returns_empty_payload(self):
        empty = CharplotJourney.objects.create(
            user=self.user, title="空旅程", input_type="text"
        )
        self.assertEqual(build_skill_tree(empty), {"nodes": [], "edges": []})


class SkillTreeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.client.force_login(self.user)
        self.journey = CharplotJourney.objects.create(
            user=self.user, title="装饰器旅程", input_type="text"
        )
        chapter = CharplotChapter.objects.create(
            journey=self.journey, title="函数基础", order=0
        )
        self.kp1 = CharplotKnowledgePoint.objects.create(
            chapter=chapter, title="函数是一等公民", order=0
        )
        self.kp2 = CharplotKnowledgePoint.objects.create(
            chapter=chapter, title="闭包", order=1
        )
        self.kp2.prerequisites.add(self.kp1)
        self.url = SKILL_TREE_URL.format(id=self.journey.id)

    def test_skill_tree_returns_graph_payload(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(len(payload["nodes"]), 2)
        self.assertEqual(len(payload["edges"]), 1)
        statuses = {n["title"]: n["status"] for n in payload["nodes"]}
        self.assertEqual(statuses["函数是一等公民"], "unlocked")
        self.assertEqual(statuses["闭包"], "locked")

    def test_skill_tree_other_users_journey_404(self):
        other = create_user("bob")
        other_journey = CharplotJourney.objects.create(
            user=other, title="他人旅程", input_type="text"
        )
        resp = self.client.get(SKILL_TREE_URL.format(id=other_journey.id))
        self.assertEqual(resp.status_code, 404)

    def test_skill_tree_anonymous_forbidden(self):
        anon = APIClient()
        resp = anon.get(self.url)
        self.assertEqual(resp.status_code, 403)
