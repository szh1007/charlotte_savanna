"""知识库索引任务测试 (Issue 09, DESIGN §4.2 /ai/kb/index, CONTRACT.md §6.5).

覆盖: 成功流 (parsing → chunking x N → embedding x N → indexing → done,
per-doc 假进度 + 落库) / 未抢占 (no_documents/offline/indexing) 直接
done 不落库 / claim 异常 → error + 失败标记 / save 异常 → error 兜底 /
task_type == "kb-index". 内部端点 (Django 侧) 由 monkeypatch 隔离.
"""

import pytest
from tests.conftest import wait_task_status
from tests.test_tasks_sse import read_stream

from project.charplot.api import tasks

# claim 返回的文档清单 (2 个有效文档)
KB_DOCUMENTS = [
    {
        "id": 1,
        "title": "a.pdf",
        "filename": "a.pdf",
        "file_size": 1024,
        "extension": "pdf",
    },
    {
        "id": 2,
        "title": "b.md",
        "filename": "b.md",
        "file_size": 512,
        "extension": "md",
    },
]


@pytest.fixture
def mock_kb_endpoints(monkeypatch):
    """隔离索引内部端点, 记录 claim / save / failed 调用."""
    calls = {"claim": [], "save": [], "failed": []}

    async def fake_claim(kb_id, task_id):
        calls["claim"].append((kb_id, task_id))
        return True, {"documents": KB_DOCUMENTS}

    async def fake_save(kb_id, task_id):
        calls["save"].append((kb_id, task_id))

    async def fake_failed(kb_id, task_id, error_message):
        calls["failed"].append((kb_id, task_id, error_message))

    monkeypatch.setattr(tasks, "claim_kb_index", fake_claim)
    monkeypatch.setattr(tasks, "save_kb_index_success", fake_save)
    monkeypatch.setattr(tasks, "mark_kb_index_failed", fake_failed)
    return calls


def start_index(client, kb_id=1):
    resp = client.post("/ai/kb/index", json={"kb_id": kb_id})
    assert resp.status_code == 200
    return resp.json()["task_id"]


def test_index_success_stream_and_save(client, mock_kb_endpoints):
    """成功流: per-doc 假进度 (切分/向量化各 N 段) → 落库 → done."""
    task_id = start_index(client)
    assert wait_task_status(client, task_id, "done")

    events = read_stream(client, task_id)
    # per-doc 交替: 每文档先切分后向量化 (流水线语义), 进度单调递增
    assert [e[2]["stage"] for e in events] == [
        "parsing",
        "chunking",
        "embedding",
        "chunking",
        "embedding",
        "indexing",
        "done",
    ]
    assert {e[1] for e in events} == {"pipeline-progress"}  # 事件名统一
    assert events[-1][2]["progress"] == 100
    # 进度单调递增
    progresses = [e[2]["progress"] for e in events]
    assert progresses == sorted(progresses)
    # per-doc 消息带文件名
    assert "a.pdf" in events[1][2]["message"]

    # 落库恰好 1 次 (kb_id, task_id)
    assert len(mock_kb_endpoints["save"]) == 1
    assert mock_kb_endpoints["save"][0] == (1, task_id)
    assert mock_kb_endpoints["failed"] == []


def test_task_type_is_kb_index(client, mock_kb_endpoints):
    task_id = start_index(client)
    assert wait_task_status(client, task_id, "done")
    resp = client.get(f"/ai/tasks/{task_id}")
    assert resp.json()["task_type"] == "kb-index"


def test_claim_skipped_done_without_save(client, mock_kb_endpoints, monkeypatch):
    """未抢占 (无文档/下线/索引中): 任务直接 done, 不落库."""
    for reason in ("no_documents", "offline", "indexing"):

        async def fake_claim(kb_id, task_id, _reason=reason):
            return False, {"reason": _reason}

        monkeypatch.setattr(tasks, "claim_kb_index", fake_claim)
        task_id = start_index(client)
        assert wait_task_status(client, task_id, "done")

        events = read_stream(client, task_id)
        assert events[-1][2]["stage"] == "done"
        assert reason in events[-1][2]["message"]  # "索引跳过 (reason)"
        assert mock_kb_endpoints["save"] == []


def test_claim_error_marks_failed(client, mock_kb_endpoints, monkeypatch):
    """claim 异常 (Django 不可达/拒绝): error + 失败标记恰 1 次."""

    async def fake_claim(kb_id, task_id):
        raise RuntimeError("索引抢占失败")

    monkeypatch.setattr(tasks, "claim_kb_index", fake_claim)
    task_id = start_index(client)
    assert wait_task_status(client, task_id, "error")

    events = read_stream(client, task_id)
    assert events[-1][2]["stage"] == "error"
    assert "索引抢占失败" in events[-1][2]["message"]
    assert len(mock_kb_endpoints["failed"]) == 1
    assert mock_kb_endpoints["save"] == []


def test_save_error_marks_failed(client, mock_kb_endpoints, monkeypatch):
    """落库失败 (transient 重试后): error + mark 兜底 (Django 侧已有 mark)."""

    async def fake_save(kb_id, task_id):
        raise RuntimeError("落库失败")

    monkeypatch.setattr(tasks, "save_kb_index_success", fake_save)
    task_id = start_index(client)
    assert wait_task_status(client, task_id, "error")
    assert len(mock_kb_endpoints["failed"]) == 1
