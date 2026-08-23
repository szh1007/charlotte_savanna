"""出题任务测试 (Issue 08, DESIGN §4.2 /ai/levels/generate).

覆盖: 成功流 (preparing→generating→saving→done, 新题+复习题拼接落库) /
未抢占直接 done 不调 LLM / LLM 输出非法触发重试 / LLM 异常 → error + 失败
标记 / 落库失败 → error. 内部端点 (Django 侧) 由 monkeypatch 隔离.
"""

import pytest
from tests.conftest import wait_task_status
from tests.fakes import FakeChatModel
from tests.test_tasks_sse import read_stream

from project.charplot.api import tasks
from project.charplot.pipeline import llm as pipeline_llm

# 出题输入 (claim 返回; new_count 与 FakeChatModel.QUESTIONS_JSON 题数一致)
GENERATION_INPUT = {
    "journey_id": 1,
    "level_id": 10,
    "level_seq": 2,
    "level_type": "regular",
    "difficulty": "medium",
    "question_count": 6,
    "new_count": 5,
    "kp": {"id": 1, "title": "闭包", "summary": "词法作用域", "prereq_titles": []},
    "chapter": {"id": 1, "title": "基础", "summary": ""},
    "kp_infos": [
        {"id": 1, "title": "闭包", "summary": "词法作用域", "prereq_titles": []}
    ],
    "review_questions": [
        {
            "source_kp_id": 2,
            "question_type": "judge",
            "content": "历史复习题: 装饰器只能用于函数?",
            "options": [],
            "answer": ["false"],
            "explanation": "历史讲解",
            "sources": [],
        }
    ],
}


@pytest.fixture
def mock_level_endpoints(monkeypatch):
    """隔离出题内部端点, 记录 claim / save / failed 调用."""
    calls = {"claim": [], "save": [], "failed": []}

    async def fake_claim(journey_id, level_seq, task_id):
        calls["claim"].append((journey_id, level_seq, task_id))
        return True, {"input": GENERATION_INPUT}

    async def fake_save(journey_id, level_seq, task_id, questions):
        calls["save"].append((journey_id, level_seq, task_id, questions))

    async def fake_failed(journey_id, level_seq, task_id, error_message):
        calls["failed"].append((journey_id, level_seq, task_id, error_message))

    monkeypatch.setattr(tasks, "claim_level_generation", fake_claim)
    monkeypatch.setattr(tasks, "save_level_questions", fake_save)
    monkeypatch.setattr(tasks, "mark_level_generation_failed", fake_failed)
    return calls


def start_generation(client):
    resp = client.post("/ai/levels/generate", json={"journey_id": 1, "level_seq": 2})
    assert resp.status_code == 200
    return resp.json()["task_id"]


def test_generation_success_streams_stages_and_saves(client, mock_level_endpoints):
    task_id = start_generation(client)
    assert wait_task_status(client, task_id, "done")

    events = read_stream(client, task_id)
    assert [e[2]["stage"] for e in events] == [
        "preparing",
        "generating",
        "saving",
        "done",
    ]
    assert {e[1] for e in events} == {"pipeline-progress"}  # 事件名统一
    assert events[-1][2]["progress"] == 100

    # 落库收到 5 道新题 + 1 道复习题 (复习题透传未改, 固定置于末尾)
    assert len(mock_level_endpoints["save"]) == 1
    _, seq, task_id_saved, questions = mock_level_endpoints["save"][0]
    assert seq == 2
    assert task_id_saved == task_id
    assert len(questions) == 6
    assert questions[0]["question_type"] == "choice"
    assert questions[-1] == GENERATION_INPUT["review_questions"][0]


def test_claim_skipped_when_level_ready(client, mock_level_endpoints, monkeypatch):
    """未抢占 (已就绪/已有任务): 任务直接 done, 不调 LLM."""

    async def fake_claim(journey_id, level_seq, task_id):
        return False, {"reason": "ready"}

    monkeypatch.setattr(tasks, "claim_level_generation", fake_claim)
    fake_model = FakeChatModel()
    monkeypatch.setattr(pipeline_llm, "get_chat_model", lambda: fake_model)

    task_id = start_generation(client)
    assert wait_task_status(client, task_id, "done")
    assert fake_model.calls == []  # 未调用 LLM
    assert mock_level_endpoints["save"] == []


def test_llm_error_marks_level_failed(client, mock_level_endpoints, monkeypatch):
    async def fake_generate(input_data):
        raise RuntimeError("LLM 超时")

    monkeypatch.setattr(tasks, "generate_level_questions", fake_generate)
    task_id = start_generation(client)
    assert wait_task_status(client, task_id, "error")

    events = read_stream(client, task_id)
    assert events[-1][2]["stage"] == "error"
    assert "LLM 超时" in events[-1][2]["message"]
    assert len(mock_level_endpoints["failed"]) == 1
    assert mock_level_endpoints["save"] == []


def test_invalid_llm_output_retries_then_succeeds(
    client, mock_level_endpoints, monkeypatch
):
    """LLM 首次输出非法 JSON → 带错误反馈重试 → 修正成功."""
    fake_model = FakeChatModel(fail_first_n=1)
    monkeypatch.setattr(pipeline_llm, "get_chat_model", lambda: fake_model)

    task_id = start_generation(client)
    assert wait_task_status(client, task_id, "done")
    assert len(fake_model.calls) == 2  # 首次非法 + 重试
    assert "上次输出校验失败" in fake_model.calls[1]
    assert len(mock_level_endpoints["save"]) == 1


def test_invalid_llm_output_retries_exhausted_error(
    client, mock_level_endpoints, monkeypatch
):
    fake_model = FakeChatModel(fail_first_n=10)
    monkeypatch.setattr(pipeline_llm, "get_chat_model", lambda: fake_model)

    task_id = start_generation(client)
    assert wait_task_status(client, task_id, "error")
    assert mock_level_endpoints["save"] == []
    assert len(mock_level_endpoints["failed"]) == 1


def test_save_failure_marks_error(client, mock_level_endpoints, monkeypatch):
    async def fake_save(journey_id, level_seq, task_id, questions):
        raise RuntimeError("Django 不可达")

    monkeypatch.setattr(tasks, "save_level_questions", fake_save)
    task_id = start_generation(client)
    assert wait_task_status(client, task_id, "error")
    events = read_stream(client, task_id)
    assert events[-1][2]["stage"] == "error"
    assert len(mock_level_endpoints["failed"]) == 1


def test_level_generation_task_type_in_status(client, mock_level_endpoints):
    task_id = start_generation(client)
    assert wait_task_status(client, task_id, "done")
    resp = client.get(f"/ai/tasks/{task_id}")
    assert resp.json()["task_type"] == "level-generation"
