"""SSE 进度流测试 (Issue 03, DESIGN §4.2 / CONTRACT.md §2).

覆盖: 完整流事件序 + 帧 id 递增 + 终端事件后流结束; 断线重连全量重放;
Last-Event-ID 增量续推; 未知任务 404.
"""

from httpx_sse import connect_sse
from tests.conftest import wait_task_status
from tests.test_pipeline_api import start_pipeline

EXPECTED_STAGES = ["parsing", "analyzing", "searching", "deconstructing", "done"]
EXPECTED_PROGRESS = [15, 35, 60, 90, 100]


def read_stream(client, task_id, headers=None):
    """完整读取 SSE 流直至结束, 返回事件列表 [(id, event, json)]."""
    events = []
    with connect_sse(
        client, "GET", f"/ai/tasks/{task_id}/events", headers=headers or {}
    ) as sse:
        for sse_event in sse.iter_sse():
            events.append((sse_event.id, sse_event.event, sse_event.json()))
    return events


def finished_task(client, mock_django_save):
    """创建任务并等待 done."""
    task_id = start_pipeline(client).json()["task_id"]
    assert wait_task_status(client, task_id, "done")
    return task_id


def test_sse_streams_five_stages_in_order(client, mock_django_save):
    task_id = finished_task(client, mock_django_save)
    events = read_stream(client, task_id)

    assert len(events) == 5
    assert [e[2]["stage"] for e in events] == EXPECTED_STAGES
    assert [e[2]["progress"] for e in events] == EXPECTED_PROGRESS
    # 帧 id 递增 (0..4), event 名固定
    assert [e[0] for e in events] == ["0", "1", "2", "3", "4"]
    assert {e[1] for e in events} == {"pipeline-progress"}
    # 载荷契约字段
    assert all({"task_id", "stage", "progress", "message"} <= set(e[2]) for e in events)
    assert events[-1][2]["stage"] == "done"


def test_sse_reconnect_replays_all_events(client, mock_django_save):
    task_id = finished_task(client, mock_django_save)
    first = read_stream(client, task_id)
    second = read_stream(client, task_id)
    assert [e[2]["stage"] for e in first] == [e[2]["stage"] for e in second]


def test_sse_last_event_id_resumes_incrementally(client, mock_django_save):
    task_id = finished_task(client, mock_django_save)
    read_stream(client, task_id)  # 消费全部事件

    # 模拟断线重连: 已收到 id=2 (searching), 重连只应收 3,4
    resumed = read_stream(client, task_id, headers={"Last-Event-ID": "2"})
    assert [e[0] for e in resumed] == ["3", "4"]
    assert [e[2]["stage"] for e in resumed] == ["deconstructing", "done"]


def test_sse_unknown_task_404(client):
    resp = client.get("/ai/tasks/does-not-exist/events")
    assert resp.status_code == 404
