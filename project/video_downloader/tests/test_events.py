"""T03 SSE 进度流验收测试 (HTTP seam, 引擎 mock, 自定义 ASGI 流式客户端).

httpx ASGITransport 不支持流式响应, 故用 SseStream 直接驱动 app 消费事件流
(见 sse_client.py); 创建任务等普通请求仍走 TestClient.
"""

import json
import time
from collections.abc import Callable

from backend import main
from backend.events import bus
from backend.routers import events as events_mod
from backend.task_manager import STATUS_COMPLETED
from fastapi.testclient import TestClient
from helpers import create_download, find_task, wait_until
from sse_client import SseStream


def _parse_frame(frame: str) -> dict | None:
    """解析单个 SSE 帧字符串 → {event, data}, 空帧返回 None."""
    evt: dict = {}
    for line in frame.splitlines():
        if line.startswith("event: "):
            evt["event"] = line.removeprefix("event: ")
        elif line.startswith("data: "):
            evt["data"] = json.loads(line.removeprefix("data: "))
    return evt or None


def wait_events(
    stream: SseStream, predicate: Callable[[dict], bool], timeout: float = 2.0
) -> list[dict]:
    """消费 SSE 帧直到谓词满足, 返回已解析的全部事件 (超时断言失败)."""
    events: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = stream.next(timeout=0.1)
        except TimeoutError:
            continue
        evt = _parse_frame(frame)
        if evt:
            events.append(evt)
            if predicate(evt):
                return events
    raise AssertionError(f"SSE 事件在 {timeout}s 内未满足谓词, 已收到 {len(events)} 帧")


def test_events_connection_returns_event_stream() -> None:
    """验收: SSE 连接可建立, 响应为 text/event-stream 且连接保持."""
    stream = SseStream(main.app, "/api/events")
    stream.wait_headers()
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    stream.close()


def test_events_pushes_status_updates(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 任务状态变化推送 task-update, data 含完整字段与直链地址."""
    stream = SseStream(main.app, "/api/events")
    task_id = create_download(client, "https://www.bilibili.com/video/av1", "22")

    events = wait_events(
        stream, lambda e: e.get("data", {}).get("status") == STATUS_COMPLETED
    )
    task_events = [e["data"] for e in events if e["data"]["task_id"] == task_id]
    statuses = [e["status"] for e in task_events]
    # 状态流转顺序: resolving → queued → downloading → completed
    # (连接时无任务, 全部事件来自推送)
    assert statuses.index("resolving") < statuses.index("queued")
    assert statuses.index("queued") < statuses.index("downloading")
    assert statuses.index("downloading") < statuses.index("completed")

    # 进度 hook 上报事件 (scheduler 置 downloading 时 progress 为 0, hook 后为 50)
    downloading_events = [e for e in task_events if e["status"] == "downloading"]
    assert any(
        e["progress"] == 50.0 and e["message"] == "下载中 50%"
        for e in downloading_events
    )
    completed = task_events[-1]
    assert completed["progress"] == 100.0
    assert completed["message"] == "下载完成"
    assert completed["url"] == f"/api/files/{task_id}"
    assert completed["error"] is None
    # 元信息随事件携带 (前端据此补全卡片标题/封面, bugfix/0006)
    assert completed["title"] == "测试视频标题"
    assert completed["cover"] == "https://example.com/cover.jpg"
    stream.close()


def test_events_initial_snapshot(
    client: TestClient, fake_extract, fake_download
) -> None:
    """连接建立即推送已存在任务的当前状态 (断线重连恢复现场)."""
    task_id = create_download(client, "https://www.bilibili.com/video/av2", "22")
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)

    stream = SseStream(main.app, "/api/events")
    events = wait_events(stream, lambda e: "data" in e)
    assert events[0]["event"] == "task-update"
    assert events[0]["data"]["task_id"] == task_id
    assert events[0]["data"]["status"] == STATUS_COMPLETED
    stream.close()


def test_events_heartbeat(monkeypatch) -> None:
    """验收: 空闲连接周期收到心跳事件 (间隔常量缩短验证机制)."""
    monkeypatch.setattr(events_mod, "HEARTBEAT_INTERVAL", 0.1)
    stream = SseStream(main.app, "/api/events")
    assert wait_events(stream, lambda e: e.get("event") == "heartbeat")
    stream.close()


def test_events_disconnect_cleans_subscription(
    client: TestClient, fake_extract
) -> None:
    """验收: 客户端断开后订阅被清理, 无泄漏."""
    stream = SseStream(main.app, "/api/events")
    assert wait_until(lambda: len(bus._subs) == 1)
    stream.close()
    # 断开后发布一次事件, 唤醒服务端生成器走到 finally 完成清理
    client.post("/api/resolve", json={"url": "https://www.bilibili.com/video/av3"})
    assert wait_until(lambda: len(bus._subs) == 0)
    stream.join()


def test_events_disconnect_cleanup_via_heartbeat(monkeypatch) -> None:
    """断开清理慢路径: 无事件时由下一次心跳触发 (send 失败 → 生成器 finally)."""
    monkeypatch.setattr(events_mod, "HEARTBEAT_INTERVAL", 0.1)
    stream = SseStream(main.app, "/api/events")
    assert wait_until(lambda: len(bus._subs) == 1)
    stream.close()
    # 心跳路径清理, 无需事件唤醒
    assert wait_until(lambda: len(bus._subs) == 0)
    stream.join()


def test_events_task_ids_filter(
    client: TestClient, fake_extract, fake_download
) -> None:
    """PRD 契约: ?task_ids 只推送关注任务的事件 (快照与广播均过滤)."""
    stream = SseStream(main.app, "/api/events?task_ids=1")
    id1 = create_download(client, "https://www.bilibili.com/video/av4", "22")
    # 任务 2: 应被过滤 (不投递事件)
    create_download(client, "https://www.bilibili.com/video/av5", "18")

    events = wait_events(
        stream,
        lambda e: (
            e.get("data", {}).get("task_id") == id1
            and e["data"]["status"] == STATUS_COMPLETED
        ),
    )
    # 推送到连接的事件全部属于任务 1 (任务 2 的事件被过滤, 不投递)
    assert all(e["data"]["task_id"] == id1 for e in events if "data" in e)
    stream.close()


def test_events_invalid_task_ids_returns_422() -> None:
    """非整数 task_ids 参数: 422 明确错误."""
    stream = SseStream(main.app, "/api/events?task_ids=abc")
    stream.wait_headers()
    assert stream.status_code == 422
    stream.close()
