"""测试共享工具函数 (fixtures 见 conftest.py)."""

import threading
import time
from collections.abc import Callable

from backend import task_manager as tm
from conftest import MEMBER_KEY
from fastapi.testclient import TestClient


def wait_until(cond: Callable[[], bool], timeout: float = 2.0) -> bool:
    """轮询直到条件成立或超时 (调度线程异步, 测试需等待)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def wait_downloads_settle(release: threading.Event, timeout: float = 5.0) -> bool:
    """放行下载 + 停止调度器, 等待所有已派发 worker 结束 (测试尾部清理).

    防止 worker 跨测试残留: 下个测试清空任务存储后, 残留 worker 更新状态会
    抛 KeyError (PytestUnhandledThreadExceptionWarning). 停止调度器同时避免
    排队任务继续被派发; join 等待调度器线程真正退出, 否则下个测试
    ensure_scheduler 会误判「仍存活」而不重启 (调度器消失, 任务永驻 queued).
    """
    release.set()
    tm.manager.stop_scheduler()
    ok = wait_until(lambda: tm.manager._active == {False: 0, True: 0}, timeout)
    scheduler = tm.manager._scheduler
    if scheduler is not None:
        scheduler.join(timeout=1.0)
    return ok


def create_download(client: TestClient, url: str, format_id: str) -> int:
    """POST /api/downloads 并断言 200, 返回 task_id."""
    resp = client.post("/api/downloads", json={"url": url, "format_id": format_id})
    assert resp.status_code == 200
    return resp.json()["task_id"]


def member_headers(client: TestClient) -> dict[str, str]:
    """提交会员密钥换取会话 token, 返回会员请求头 (密钥见 conftest.MEMBER_KEY)."""
    resp = client.post("/api/member", json={"key": MEMBER_KEY})
    assert resp.status_code == 200
    return {"X-Member-Token": resp.json()["token"]}


def find_task(client: TestClient, task_id: int) -> dict:
    """从 GET /api/tasks 结果中查找任务, 未找到则断言失败."""
    for t in client.get("/api/tasks").json()["tasks"]:
        if t["task_id"] == task_id:
            return t
    raise AssertionError(f"task {task_id} not found")
