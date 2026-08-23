"""/ai/pipeline 与 /ai/tasks/{id} 测试 (Issue 03).

覆盖: 任务创建 / 状态迁移 running→done / 入参校验 (text 缺 content 422) /
管道异常 → error + 失败标记 / 落库失败 → error.
"""

from tests.conftest import wait_task_status

from project.charplot.api import tasks

PIPELINE_URL = "/ai/pipeline"


def start_pipeline(client, **overrides):
    payload = {"journey_id": 1, "input_type": "text", "content": "我想学 Python 装饰器"}
    payload.update(overrides)
    return client.post(PIPELINE_URL, json=payload)


def test_start_pipeline_returns_task_id(client, mock_django_save):
    resp = start_pipeline(client)
    assert resp.status_code == 200
    body = resp.json()
    assert "task_id" in body
    assert len(body["task_id"]) == 32  # uuid4().hex


def test_task_status_transitions_running_to_done(client, mock_django_save):
    task_id = start_pipeline(client).json()["task_id"]

    resp = client.get(f"/ai/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"

    assert wait_task_status(client, task_id, "done")

    body = client.get(f"/ai/tasks/{task_id}").json()
    assert body["status"] == "done"
    assert body["stage"] == "done"
    assert body["progress"] == 100
    assert body["error_message"] is None


def test_text_input_without_content_rejected(client):
    resp = client.post(PIPELINE_URL, json={"journey_id": 1, "input_type": "text"})
    assert resp.status_code == 422


def test_kb_input_without_kb_id_rejected(client):
    resp = client.post(PIPELINE_URL, json={"journey_id": 1, "input_type": "kb"})
    assert resp.status_code == 422
    assert "必须提供 kb_id" in resp.json()["detail"][0]["msg"]


def test_kb_id_on_non_kb_input_rejected(client):
    resp = client.post(
        PIPELINE_URL,
        json={"journey_id": 1, "input_type": "text", "content": "x", "kb_id": 7},
    )
    assert resp.status_code == 422
    assert "仅知识库输入" in resp.json()["detail"][0]["msg"]


def test_kb_input_full_flow_done(client, monkeypatch, mock_django_save):
    """kb 旅程 API 全链路: 任务创建 → done → 图谱落库 (两轮解构假件)."""
    from tests.fakes import FakeChatModel
    from tests.test_pipeline import (
        KB_CHUNKS,
        KB_META_READY,
        KB_SKELETON_JSON,
        kb_refine_responder,
    )

    from project.charplot.api import django_client
    from project.charplot.pipeline import llm as pipeline_llm
    from project.charplot.pipeline.sources import kb_source

    async def fake_meta(kb_id):
        return KB_META_READY

    fake = FakeChatModel(
        sequence=[
            ("请为以下知识库构建图谱骨架", KB_SKELETON_JSON),
            ("请细化以下知识点的依赖边与摘要", kb_refine_responder),
        ]
    )
    monkeypatch.setattr(pipeline_llm, "get_chat_model", lambda: fake)
    monkeypatch.setattr(django_client, "fetch_kb_meta", fake_meta)
    monkeypatch.setattr(
        kb_source, "search_kb", lambda kb_id, query, top_k=None: KB_CHUNKS
    )

    resp = client.post(
        PIPELINE_URL,
        json={"journey_id": 1, "input_type": "kb", "kb_id": 7},
    )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    assert wait_task_status(client, task_id, "done")
    # 图谱落库 (mock 记录): graph 为契约 v1
    saved = [c for c in mock_django_save if c[0] == "save"]
    assert len(saved) == 1
    assert saved[0][3]["version"] == 1


def test_pipeline_exception_sets_task_error(client, monkeypatch):
    async def boom(inp, emit):
        raise RuntimeError("stub 管道崩溃")

    monkeypatch.setattr(tasks, "run_pipeline", boom)
    task_id = start_pipeline(client).json()["task_id"]

    assert wait_task_status(client, task_id, "error")
    body = client.get(f"/ai/tasks/{task_id}").json()
    assert "stub 管道崩溃" in body["error_message"]


def test_pipeline_exception_marks_journey_failed(client, monkeypatch, mock_django_save):
    async def boom(inp, emit):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "run_pipeline", boom)
    task_id = start_pipeline(client).json()["task_id"]

    assert wait_task_status(client, task_id, "error")
    # 失败标记经内部端点调用 (mock 记录)
    assert any(c[0] == "failed" for c in mock_django_save)


def test_save_failure_sets_task_error(client, monkeypatch):
    async def fail_save(journey_id, task_id, graph):
        raise RuntimeError("Django 不可达")

    monkeypatch.setattr(tasks, "save_graph_to_django", fail_save)
    task_id = start_pipeline(client).json()["task_id"]

    assert wait_task_status(client, task_id, "error")
    body = client.get(f"/ai/tasks/{task_id}").json()
    assert "Django 不可达" in body["error_message"]


def test_unknown_task_404(client):
    resp = client.get("/ai/tasks/does-not-exist")
    assert resp.status_code == 404
