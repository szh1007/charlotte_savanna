"""知识库管理链路测试 (Issue 09, PRD C-1~C-4).

覆盖: 服务层 (格式校验/创建/批量事务/claim 状态机矩阵/下线上下线) +
API 权限矩阵 (仅 is_staff 可管理) + 文档软删恢复 + 内部端点三连
(index-claim/index-save/index-failed) + topics 可见性 (仅就绪).
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from app.charplot.models import (
    CharplotKnowledgeBase,
    CharplotKnowledgeBaseDocument,
)
from app.charplot.services import (
    KnowledgeBaseStateError,
    claim_kb_index,
    create_kb_documents,
    create_knowledge_base,
    set_kb_offline,
    set_kb_online,
    validate_kb_document_file,
)

User = get_user_model()

INTERNAL_TOKEN = "test-internal-token"

KB_URL = "/api/charplot/kb/"
TOPICS_URL = "/api/charplot/topics/"


def create_user(username="alice", is_staff=False):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="TestPass#2026",
        is_staff=is_staff,
    )


def make_kb(name="RAG 实战", status=CharplotKnowledgeBase.Status.DRAFT, **kwargs):
    kb = create_knowledge_base(name=name, **kwargs)
    if status != CharplotKnowledgeBase.Status.DRAFT:
        CharplotKnowledgeBase.objects.filter(pk=kb.pk).update(status=status)
        kb.refresh_from_db()
    return kb


def upload_files(kb, files):
    """服务层直调批量落库 (返回文档列表), 测试工厂."""
    return create_kb_documents(
        kb,
        [
            SimpleUploadedFile(name, content, content_type="application/octet-stream")
            for name, content in files
        ],
    )


def make_upload(name, content=b"fake content"):
    return SimpleUploadedFile(name, content, content_type="application/octet-stream")


# ---------------------------------------------------------------------------
# A. 服务层
# ---------------------------------------------------------------------------


class ValidateDocumentFileTests(TestCase):
    def test_allowed_extensions_pass(self):
        for name in ("a.pdf", "b.docx", "c.pptx", "d.md", "e.txt", "f.html"):
            self.assertEqual(validate_kb_document_file(make_upload(name)), name)

    def test_uppercase_extension_pass(self):
        # 扩展名大小写不敏感
        self.assertEqual(validate_kb_document_file(make_upload("A.PDF")), "A.PDF")

    def test_disallowed_extension_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持的文档格式"):
            validate_kb_document_file(make_upload("malware.exe"))

    def test_no_extension_rejected(self):
        with self.assertRaises(ValueError):
            validate_kb_document_file(make_upload("noext"))

    @patch("app.charplot.services.KB_MAX_FILE_SIZE_MB", 0)
    def test_oversize_rejected(self):
        with self.assertRaisesRegex(ValueError, "文档过大"):
            validate_kb_document_file(make_upload("big.pdf", b"x" * 10))


class CreateKnowledgeBaseTests(TestCase):
    def test_create_marks_draft_and_collection(self):
        kb = create_knowledge_base(
            "RAG 实战", description="知识库", cover="https://x/y.png"
        )
        self.assertEqual(kb.status, CharplotKnowledgeBase.Status.DRAFT)
        self.assertEqual(kb.collection_name, f"cp_kb_{kb.id}")
        self.assertEqual(kb.description, "知识库")
        self.assertEqual(kb.cover, "https://x/y.png")


class CreateKbDocumentsTests(TestCase):
    def test_batch_create_all_documents(self):
        kb = make_kb()
        docs = upload_files(kb, [("a.pdf", b"pdf"), ("b.md", b"md")])
        self.assertEqual(len(docs), 2)
        self.assertEqual(CharplotKnowledgeBaseDocument.objects.count(), 2)
        self.assertTrue(docs[0].file.name.startswith("app/charplot/uploads/kb/"))
        self.assertEqual(docs[0].file_size, 3)
        self.assertFalse(docs[0].is_deleted)

    def test_invalid_middle_file_rolls_back_all(self):
        kb = make_kb()
        with self.assertRaises(ValueError):
            upload_files(kb, [("a.pdf", b"pdf"), ("bad.exe", b"exe")])
        # all-or-nothing: 非法文件导致整批回滚
        self.assertEqual(CharplotKnowledgeBaseDocument.objects.count(), 0)


class ClaimKbIndexTests(TestCase):
    def setUp(self):
        self.kb = make_kb()
        upload_files(self.kb, [("a.pdf", b"pdf")])

    def claim(self, kb, task_id="task-1"):
        return claim_kb_index(kb, task_id)

    def test_draft_claimed(self):
        claimed, payload = self.claim(self.kb)
        self.assertTrue(claimed)
        docs = payload["documents"]
        self.assertEqual(len(docs), 1)
        doc = docs[0]
        self.assertIn("id", doc)
        self.assertEqual(doc["filename"], "a.pdf")
        self.assertEqual(doc["extension"], "pdf")
        self.assertEqual(doc["file_size"], 3)
        self.kb.refresh_from_db()
        self.assertEqual(self.kb.status, CharplotKnowledgeBase.Status.INDEXING)
        self.assertEqual(self.kb.latest_task_id, "task-1")

    def test_failed_claimed_retry(self):
        self.kb.status = CharplotKnowledgeBase.Status.FAILED
        self.kb.error_message = "上次失败"
        self.kb.save()
        claimed, _ = self.claim(self.kb, task_id="task-2")
        self.assertTrue(claimed)
        self.kb.refresh_from_db()
        self.assertEqual(self.kb.status, CharplotKnowledgeBase.Status.INDEXING)
        self.assertEqual(self.kb.error_message, "")  # 重试清空失败原因

    def test_ready_claimed_full_rebuild(self):
        # 全量重建 (Q18b): ready 也可重新索引
        self.kb.status = CharplotKnowledgeBase.Status.READY
        self.kb.save()
        claimed, _ = self.claim(self.kb)
        self.assertTrue(claimed)

    def test_offline_rejected(self):
        self.kb.status = CharplotKnowledgeBase.Status.OFFLINE
        self.kb.save()
        claimed, payload = self.claim(self.kb)
        self.assertFalse(claimed)
        self.assertEqual(payload["reason"], "offline")

    def test_no_documents_rejected(self):
        empty = make_kb(name="空库")
        claimed, payload = self.claim(empty)
        self.assertFalse(claimed)
        self.assertEqual(payload["reason"], "no_documents")

    def test_indexing_rejected_while_running(self):
        self.claim(self.kb, task_id="task-1")
        claimed, payload = self.claim(self.kb, task_id="task-2")
        self.assertFalse(claimed)
        self.assertEqual(payload["reason"], "indexing")
        self.assertEqual(payload["task_id"], "task-1")

    def test_stale_indexing_can_reclaim(self):
        # FastAPI 崩溃后任务丢失: 索引中状态超过陈旧阈值 → 可重新抢占
        self.claim(self.kb, task_id="task-1")
        CharplotKnowledgeBase.objects.filter(pk=self.kb.pk).update(
            updated_at=timezone.now() - timedelta(minutes=11)
        )
        claimed, _ = self.claim(self.kb, task_id="task-2")
        self.assertTrue(claimed)
        self.kb.refresh_from_db()
        self.assertEqual(self.kb.latest_task_id, "task-2")

    def test_deleted_documents_excluded_from_claim_input(self):
        # 双文档: 软删 1 个 → 清单仅剩未删除的
        upload_files(self.kb, [("b.md", b"md")])
        docs = list(self.kb.documents.all())
        self.kb.documents.filter(pk=docs[0].pk).update(is_deleted=True)
        claimed, payload = self.claim(self.kb)
        self.assertTrue(claimed)
        self.assertEqual([d["id"] for d in payload["documents"]], [docs[1].id])

    def test_all_deleted_means_no_documents(self):
        # 全部软删 → 无有效文档 → 拒绝 (防止"就绪但零内容")
        self.kb.documents.all().update(is_deleted=True)
        claimed, payload = self.claim(self.kb)
        self.assertFalse(claimed)
        self.assertEqual(payload["reason"], "no_documents")


class OfflineOnlineTests(TestCase):
    def test_ready_offline_and_back(self):
        kb = make_kb(status=CharplotKnowledgeBase.Status.READY)
        set_kb_offline(kb)
        kb.refresh_from_db()
        self.assertEqual(kb.status, CharplotKnowledgeBase.Status.OFFLINE)
        set_kb_online(kb)
        kb.refresh_from_db()
        self.assertEqual(kb.status, CharplotKnowledgeBase.Status.READY)

    def test_non_ready_cannot_offline(self):
        for status in (
            CharplotKnowledgeBase.Status.DRAFT,
            CharplotKnowledgeBase.Status.INDEXING,
            CharplotKnowledgeBase.Status.FAILED,
        ):
            kb = make_kb(status=status)
            with self.assertRaises(KnowledgeBaseStateError):
                set_kb_offline(kb)

    def test_ready_cannot_online(self):
        kb = make_kb(status=CharplotKnowledgeBase.Status.READY)
        with self.assertRaises(KnowledgeBaseStateError):
            set_kb_online(kb)


# ---------------------------------------------------------------------------
# B. API (权限 + CRUD)
# ---------------------------------------------------------------------------


class KnowledgeBaseApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = create_user("admin", is_staff=True)
        self.user = create_user("alice")

    def test_permission_matrix(self):
        # 匿名: 创建被拒 (403), 列表被拒 (403, 需登录)
        resp = self.client.post(KB_URL, {"name": "x"}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.client.get(KB_URL).status_code, 403)

        # 普通用户: 创建被拒
        self.client.force_login(self.user)
        resp = self.client.post(KB_URL, {"name": "x"}, format="json")
        self.assertEqual(resp.status_code, 403)

        # 管理员: 创建成功
        self.client.force_login(self.staff)
        resp = self.client.post(KB_URL, {"name": "RAG 实战"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["status"], "draft")

    def test_create_validation(self):
        self.client.force_login(self.staff)
        resp = self.client.post(KB_URL, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.json())
        # cover 非法 URL → 400
        resp = self.client.post(
            KB_URL, {"name": "x", "cover": "not-a-url"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cover", resp.json())

    def test_list_duplex_semantics(self):
        make_kb(name="就绪库", status=CharplotKnowledgeBase.Status.READY)
        make_kb(name="草稿库")
        make_kb(name="失败库", status=CharplotKnowledgeBase.Status.FAILED)
        make_kb(name="下线库", status=CharplotKnowledgeBase.Status.OFFLINE)
        make_kb(name="索引中", status=CharplotKnowledgeBase.Status.INDEXING)

        # 普通用户: 仅就绪
        self.client.force_login(self.user)
        resp = self.client.get(KB_URL)
        names = [k["name"] for k in resp.json()["kbs"]]
        self.assertEqual(names, ["就绪库"])

        # 管理员: 全部状态
        self.client.force_login(self.staff)
        resp = self.client.get(KB_URL)
        self.assertEqual(len(resp.json()["kbs"]), 5)

    def test_detail_requires_staff(self):
        kb = make_kb()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f"/api/charplot/kb/{kb.id}/").status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(f"/api/charplot/kb/{kb.id}/").status_code, 200)

    def test_upload_multiple_documents(self):
        self.client.force_login(self.staff)
        kb = make_kb()
        resp = self.client.post(
            f"/api/charplot/kb/{kb.id}/documents/",
            {"files": [make_upload("a.pdf"), make_upload("b.md")]},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.json()["documents"]), 2)

    def test_upload_invalid_batch_400_no_insert(self):
        self.client.force_login(self.staff)
        kb = make_kb()
        resp = self.client.post(
            f"/api/charplot/kb/{kb.id}/documents/",
            {"files": [make_upload("a.pdf"), make_upload("bad.exe")]},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不支持的文档格式", str(resp.json()))
        self.assertEqual(CharplotKnowledgeBaseDocument.objects.count(), 0)

    def test_upload_empty_files_400(self):
        self.client.force_login(self.staff)
        kb = make_kb()
        resp = self.client.post(
            f"/api/charplot/kb/{kb.id}/documents/",
            {"files": []},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)

    def test_soft_delete_and_restore(self):
        self.client.force_login(self.staff)
        kb = make_kb()
        (doc,) = upload_files(kb, [("a.pdf", b"pdf")])

        # 软删: 204 + 标记
        resp = self.client.delete(f"/api/charplot/kb/documents/{doc.id}/")
        self.assertEqual(resp.status_code, 204)
        doc.refresh_from_db()
        self.assertTrue(doc.is_deleted)
        self.assertIsNotNone(doc.deleted_at)

        # 详情分组: 有效区隐藏, 回收区可见
        detail = self.client.get(f"/api/charplot/kb/{kb.id}/").json()
        self.assertEqual(detail["documents"], [])
        self.assertEqual(len(detail["deleted_documents"]), 1)

        # 恢复: 回有效区
        resp = self.client.post(f"/api/charplot/kb/documents/{doc.id}/restore/")
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        self.assertFalse(doc.is_deleted)
        self.assertIsNone(doc.deleted_at)

    def test_offline_online_api(self):
        self.client.force_login(self.user)
        kb = make_kb(status=CharplotKnowledgeBase.Status.READY)
        # 普通用户: 下线被拒
        self.assertEqual(
            self.client.post(f"/api/charplot/kb/{kb.id}/offline/").status_code, 403
        )
        self.client.force_login(self.staff)
        resp = self.client.post(f"/api/charplot/kb/{kb.id}/offline/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "offline")
        resp = self.client.post(f"/api/charplot/kb/{kb.id}/online/")
        self.assertEqual(resp.json()["status"], "ready")
        # 草稿不可下线 → 400
        draft = make_kb(name="草稿")
        resp = self.client.post(f"/api/charplot/kb/{draft.id}/offline/")
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# C. 内部端点 (FastAPI → Django)
# ---------------------------------------------------------------------------


@override_settings(CHARPLOT_INTERNAL_TOKEN=INTERNAL_TOKEN)
class KbIndexInternalEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.kb = make_kb()
        self.docs = upload_files(self.kb, [("a.pdf", b"pdf"), ("b.md", b"md")])
        self.claim_url = f"/api/charplot/kb/{self.kb.id}/index-claim/"
        self.save_url = f"/api/charplot/kb/{self.kb.id}/index-save/"
        self.failed_url = f"/api/charplot/kb/{self.kb.id}/index-failed/"

    def claim(self, task_id="task-1"):
        return self.client.post(
            self.claim_url,
            {"task_id": task_id},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )

    def test_claim_marks_indexing_and_returns_documents(self):
        resp = self.claim()
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["claimed"])
        self.assertEqual(len(payload["documents"]), 2)
        doc = payload["documents"][0]
        self.assertEqual(doc["filename"], "a.pdf")
        self.assertEqual(doc["extension"], "pdf")
        self.kb.refresh_from_db()
        self.assertEqual(self.kb.status, CharplotKnowledgeBase.Status.INDEXING)
        self.assertEqual(self.kb.latest_task_id, "task-1")

    def test_claim_excludes_deleted_documents(self):
        self.kb.documents.filter(pk=self.docs[0].pk).update(is_deleted=True)
        payload = self.claim().json()
        self.assertEqual(len(payload["documents"]), 1)
        self.assertEqual(payload["documents"][0]["id"], self.docs[1].id)

    def test_claim_second_rejected(self):
        self.claim(task_id="task-1")
        payload = self.claim(task_id="task-2").json()
        self.assertFalse(payload["claimed"])
        self.assertEqual(payload["reason"], "indexing")
        self.assertEqual(payload["task_id"], "task-1")

    def test_claim_missing_task_id_400(self):
        resp = self.client.post(
            self.claim_url, {}, format="json", HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN
        )
        self.assertEqual(resp.status_code, 400)

    def test_claim_unknown_kb_404(self):
        resp = self.client.post(
            "/api/charplot/kb/999/index-claim/",
            {"task_id": "t"},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        self.assertEqual(resp.status_code, 404)

    def test_claim_missing_token_403(self):
        resp = self.client.post(self.claim_url, {"task_id": "t"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_claim_wrong_token_403(self):
        resp = self.client.post(
            self.claim_url,
            {"task_id": "t"},
            format="json",
            HTTP_X_INTERNAL_TOKEN="wrong",
        )
        self.assertEqual(resp.status_code, 403)

    def test_save_marks_ready(self):
        self.claim(task_id="task-1")
        resp = self.client.post(
            self.save_url,
            {"task_id": "task-1"},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")
        self.kb.refresh_from_db()
        self.assertEqual(self.kb.status, CharplotKnowledgeBase.Status.READY)
        self.assertEqual(self.kb.latest_task_id, "task-1")

    def test_failed_marks_failed(self):
        self.claim(task_id="task-1")
        resp = self.client.post(
            self.failed_url,
            {"task_id": "task-1", "error_message": "解析失败" * 500},
            format="json",
            HTTP_X_INTERNAL_TOKEN=INTERNAL_TOKEN,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "failed")
        self.kb.refresh_from_db()
        self.assertEqual(self.kb.status, CharplotKnowledgeBase.Status.FAILED)
        self.assertLessEqual(len(self.kb.error_message), 1000)  # 截断


# ---------------------------------------------------------------------------
# D. topics (用户端可见性)
# ---------------------------------------------------------------------------


class TopicsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_only_ready_appears(self):
        make_kb(name="就绪库", status=CharplotKnowledgeBase.Status.READY)
        make_kb(name="草稿库")
        make_kb(name="失败库", status=CharplotKnowledgeBase.Status.FAILED)
        make_kb(name="下线库", status=CharplotKnowledgeBase.Status.OFFLINE)
        make_kb(name="索引中", status=CharplotKnowledgeBase.Status.INDEXING)

        # 未登录可访问 (PRD A-1: 游客可浏览主题列表)
        resp = self.client.get(TOPICS_URL)
        self.assertEqual(resp.status_code, 200)
        topics = resp.json()["topics"]
        self.assertEqual([t["name"] for t in topics], ["就绪库"])
        self.assertIn("description", topics[0])
        self.assertIn("cover", topics[0])

    def test_empty_when_no_ready(self):
        make_kb(name="草稿库")
        resp = self.client.get(TOPICS_URL)
        self.assertEqual(resp.json()["topics"], [])
