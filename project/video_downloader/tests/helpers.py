"""测试共享工具函数 (fixtures 见 conftest.py)."""

import time
from collections.abc import Callable

from fastapi.testclient import TestClient


def wait_until(cond: Callable[[], bool], timeout: float = 2.0) -> bool:
    """轮询直到条件成立或超时 (调度线程异步, 测试需等待)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def create_download(client: TestClient, url: str, format_id: str) -> int:
    """POST /api/downloads 并断言 200, 返回 task_id."""
    resp = client.post("/api/downloads", json={"url": url, "format_id": format_id})
    assert resp.status_code == 200
    return resp.json()["task_id"]


def find_task(client: TestClient, task_id: int) -> dict:
    """从 GET /api/tasks 结果中查找任务, 未找到则断言失败."""
    for t in client.get("/api/tasks").json()["tasks"]:
        if t["task_id"] == task_id:
            return t
    raise AssertionError(f"task {task_id} not found")
