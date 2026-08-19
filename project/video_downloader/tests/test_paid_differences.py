"""T05 付费差异验收测试 (HTTP seam, 引擎 mock).

免费/会员差异矩阵 (PRD §5, 验收标准): 档位锁定 / 锁定档选择被拒 /
下载前重校验 / 并发槽差异 / 队列上限 429 / 会员优先调度.
会员身份通过 X-Member-Token header 识别 (T04).
"""

import json
import time
from collections.abc import Callable

from backend import main
from backend import task_manager as tm
from backend.task_manager import (
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_QUEUED,
)
from fastapi.testclient import TestClient
from helpers import (
    create_download,
    find_task,
    member_headers,
    wait_downloads_settle,
    wait_until,
)
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
    stream: SseStream, predicate: Callable[[dict], bool], timeout: float = 5.0
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


VIDEO_URL = "https://www.bilibili.com/video/av-t05"

# conftest.FAKE_INFO 档位: 18(360p) / 22(720p) / 999(1080p)
# / 999(最佳画质=最高档真实 id, bugfix/0003)
FORMAT_720P = "22"
FORMAT_1080P = "999"


def create_with(
    client: TestClient, url: str, format_id: str, headers: dict[str, str] | None = None
) -> int:
    """创建下载任务 (可选携带会员头), 断言 200 返回 task_id."""
    resp = client.post(
        "/api/downloads", json={"url": url, "format_id": format_id}, headers=headers
    )
    assert resp.status_code == 200
    return resp.json()["task_id"]


# ---- 档位锁定 (强制校验点 1) ----


def test_free_resolve_marks_high_formats_locked(
    client: TestClient, fake_extract
) -> None:
    """验收: 免费解析结果中 >720p 档位标记 locked, ≤720p 不锁, member_limited=True."""
    resp = client.post("/api/resolve", json={"url": VIDEO_URL})
    assert resp.status_code == 200
    body = resp.json()
    # 最佳画质档位指向最高档真实 id (999, bugfix/0003), 与 1080p 档位 id 相同
    assert [(f["format_id"], f["locked"]) for f in body["formats"]] == [
        ("18", False),  # 360p
        ("22", False),  # 720p
        ("999", True),  # 1080p
        ("999", True),  # 最佳画质 (1080p)
    ]
    assert body["member_limited"] is True


def test_member_resolve_unlocks_all_formats(client: TestClient, fake_extract) -> None:
    """验收: 会员解析全部档位不锁定, member_limited=False."""
    resp = client.post(
        "/api/resolve", json={"url": VIDEO_URL}, headers=member_headers(client)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(not f["locked"] for f in body["formats"])
    assert body["member_limited"] is False


# ---- 锁定档位选择被拒 (强制校验点 2) ----


def test_free_download_locked_format_rejected(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 免费用户选择 locked 档位 → 400 + 需会员提示, 任务 failed 不入队."""
    resp = client.post(
        "/api/downloads", json={"url": VIDEO_URL, "format_id": FORMAT_1080P}
    )
    assert resp.status_code == 400
    assert "会员" in resp.json()["detail"]

    task = tm.manager.list_tasks()[0]
    assert task.status == STATUS_FAILED
    assert task.format_id is None  # 未入队: 无档位记录


def test_member_download_high_format_allowed(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 会员可下载全部档位 (1080p) 并完成."""
    task_id = create_with(client, VIDEO_URL, FORMAT_1080P, member_headers(client))
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)
    task = find_task(client, task_id)
    assert task["progress"] == 100.0
    assert task["error"] is None


# ---- 下载前重校验 (强制校验点 3, 纵深防御) ----


def test_download_execution_rechecks_locked_format(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 下载执行前对非会员任务重校验档位, 绕过创建校验也会被拒.

    模拟伪造请求: 直接以免费身份构造 queued 任务 (跳过路由层档位校验),
    断言 worker 执行前拦截, 任务 failed 而非下载.
    """
    task = tm.manager.create_task(VIDEO_URL, "download", is_member=False)
    tm.manager._fill_resolved(task)  # 解析回填 formats (免费 → 1080p locked)
    tm.manager.update_status(task.id, STATUS_QUEUED, format_id=FORMAT_1080P)
    tm.manager.ensure_scheduler()

    assert wait_until(lambda: find_task(client, task.id)["status"] == STATUS_FAILED)
    assert find_task(client, task.id)["error"] == "该档位需会员解锁"
    # 引擎下载未被调用 (重校验在 downloader.download 之前)
    call_args, _release = fake_download
    assert call_args == []


# ---- 并发槽差异 (强制校验点 4) ----


def test_member_concurrency_three_slots(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 会员 3 并发槽: 4 个任务同时执行时下载数恒 ≤ 3, 第 4 个排队."""
    _call_args, release = fake_download
    release.clear()
    headers = member_headers(client)
    ids = [
        create_with(client, f"{VIDEO_URL}?c={i}", FORMAT_1080P, headers)
        for i in range(4)
    ]

    # 调度器逐轮派发 (约 0.5s/轮), 3 个进入 downloading 需数秒
    assert wait_until(
        lambda: (
            sum(1 for i in ids if find_task(client, i)["status"] == STATUS_DOWNLOADING)
            == 3
        ),
        timeout=5.0,
    )
    for _ in range(10):  # 反复采样: downloading 数恒 ≤ 3
        n = sum(1 for i in ids if find_task(client, i)["status"] == STATUS_DOWNLOADING)
        assert n <= 3
        time.sleep(0.05)
    queued = [i for i in ids if find_task(client, i)["status"] == STATUS_QUEUED]
    assert len(queued) == 1

    release.set()
    assert wait_until(
        lambda: all(find_task(client, i)["status"] == STATUS_COMPLETED for i in ids),
        timeout=5.0,
    )


def test_free_and_member_concurrency_independent(
    client: TestClient, fake_extract, fake_download
) -> None:
    """免费 1 槽与会员 3 槽相互独立: 免费 1 个 + 会员 3 个可同时下载."""
    _call_args, release = fake_download
    release.clear()
    headers = member_headers(client)
    free_id = create_download(client, f"{VIDEO_URL}?m=0", FORMAT_720P)
    member_ids = [
        create_with(client, f"{VIDEO_URL}?m={i}", FORMAT_1080P, headers)
        for i in range(1, 4)
    ]

    assert wait_until(
        lambda: all(
            find_task(client, i)["status"] == STATUS_DOWNLOADING
            for i in [free_id, *member_ids]
        ),
        timeout=5.0,
    )
    tasks = client.get("/api/tasks").json()["tasks"]
    n = sum(1 for t in tasks if t["status"] == STATUS_DOWNLOADING)
    assert n == 4

    wait_downloads_settle(release)


# ---- 队列上限 (强制校验点 4) ----


def test_free_queue_limit_returns_429(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 免费队列上限 5, 超限创建返回 429 + 明确提示."""
    _call_args, release = fake_download
    release.clear()  # 阻塞下载, 队列保持未完成状态
    for i in range(5):
        create_download(client, f"{VIDEO_URL}?q={i}", FORMAT_720P)

    resp = client.post(
        "/api/downloads", json={"url": f"{VIDEO_URL}?q=5", "format_id": FORMAT_720P}
    )
    assert resp.status_code == 429
    assert "队列已满" in resp.json()["detail"]

    wait_downloads_settle(release)


def test_member_queue_limit_returns_429(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 会员队列上限 50, 超限创建返回 429 + 明确提示."""
    _call_args, release = fake_download
    release.clear()
    headers = member_headers(client)
    for i in range(50):
        create_with(client, f"{VIDEO_URL}?q={i}", FORMAT_1080P, headers)

    resp = client.post(
        "/api/downloads",
        json={"url": f"{VIDEO_URL}?q=50", "format_id": FORMAT_1080P},
        headers=headers,
    )
    assert resp.status_code == 429
    assert "队列已满" in resp.json()["detail"]

    wait_downloads_settle(release)


def test_queue_limit_counted_per_identity(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 队列上限按身份分别计数, 会员排队任务不挤占免费配额."""
    _call_args, release = fake_download
    release.clear()
    headers = member_headers(client)
    # 会员队列排满 50 个 (全部未完成)
    for i in range(50):
        create_with(client, f"{VIDEO_URL}?i={i}", FORMAT_1080P, headers)
    # 免费配额独立: 会员队列满不影响免费用户创建 (身份过滤计数)
    for i in range(5):
        create_download(client, f"{VIDEO_URL}?f={i}", FORMAT_720P)
    # 免费自身队列超限才 429 (此时免费未完成 = 5)
    resp = client.post(
        "/api/downloads", json={"url": f"{VIDEO_URL}?f=6", "format_id": FORMAT_720P}
    )
    assert resp.status_code == 429
    assert "队列已满" in resp.json()["detail"]

    wait_downloads_settle(release)


# ---- 会员优先调度 ----


def test_member_task_scheduled_before_free(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 会员任务优先于免费任务分配并发槽.

    调度器忙循环连续派发 (有任务时毫秒级连续派发, HTTP 轮询无法捕捉时序),
    故通过 SSE 事件流断言派发顺序: member4 的 downloading 事件先于 free2.
    """
    _call_args, release = fake_download
    release.clear()
    headers = member_headers(client)

    # 占满免费槽 (1) + 会员槽 (3)
    free1 = create_download(client, f"{VIDEO_URL}?p=1", FORMAT_720P)
    assert wait_until(
        lambda: find_task(client, free1)["status"] == STATUS_DOWNLOADING, timeout=5.0
    )
    member_ids = [
        create_with(client, f"{VIDEO_URL}?p={i}", FORMAT_1080P, headers)
        for i in (2, 3, 4)
    ]
    assert wait_until(
        lambda: all(
            find_task(client, i)["status"] == STATUS_DOWNLOADING for i in member_ids
        ),
        timeout=5.0,
    )
    # 槽位已满: 后续任务只能排队
    free2 = create_download(client, f"{VIDEO_URL}?p=5", FORMAT_720P)
    member4 = create_with(client, f"{VIDEO_URL}?p=6", FORMAT_1080P, headers)
    assert wait_until(
        lambda: find_task(client, free2)["status"] == STATUS_QUEUED, timeout=5.0
    )
    assert wait_until(
        lambda: find_task(client, member4)["status"] == STATUS_QUEUED, timeout=5.0
    )

    # 订阅两个排队任务的事件流: 快照为 queued, 放行后依次收到 downloading/completed
    stream = SseStream(main.app, f"/api/events?task_ids={free2},{member4}")
    stream.wait_headers()

    done: set[int] = set()

    def _both_completed(e: dict) -> bool:
        d = e.get("data") or {}
        if d.get("status") == STATUS_COMPLETED and d.get("task_id") in (free2, member4):
            done.add(d["task_id"])
        return len(done) == 2

    release.set()
    events = wait_events(stream, _both_completed)

    def _downloading_index(task_id: int) -> int:
        for i, e in enumerate(events):
            d = e.get("data") or {}
            if d.get("task_id") == task_id and d.get("status") == "downloading":
                return i
        raise AssertionError(f"task {task_id} 未收到 downloading 事件")

    assert _downloading_index(member4) < _downloading_index(free2), (
        "会员任务未优先于免费任务派发"
    )
    stream.close()
    stream.join()
    wait_downloads_settle(release)


def test_free_task_not_starved_by_member_queue(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 会员槽满时队首的会员任务不阻塞免费任务派发 (队首阻塞回归).

    会员 3 槽占满后第 4 个会员任务排队; 此时免费槽空闲, 免费任务应被
    派发, 而非被队首会员任务永久阻塞 (旧实现 _next_queued 只取单一候选).
    """
    _call_args, release = fake_download
    release.clear()
    headers = member_headers(client)
    member_ids = [
        create_with(client, f"{VIDEO_URL}?s={i}", FORMAT_1080P, headers)
        for i in range(3)
    ]
    assert wait_until(
        lambda: all(
            find_task(client, i)["status"] == STATUS_DOWNLOADING for i in member_ids
        ),
        timeout=5.0,
    )
    member4 = create_with(client, f"{VIDEO_URL}?s=4", FORMAT_1080P, headers)
    assert wait_until(
        lambda: find_task(client, member4)["status"] == STATUS_QUEUED, timeout=5.0
    )
    free1 = create_download(client, f"{VIDEO_URL}?s=5", FORMAT_720P)
    # 免费槽空闲: 免费任务必须被派发, 不被队首的 member4 阻塞
    assert wait_until(
        lambda: find_task(client, free1)["status"] == STATUS_DOWNLOADING, timeout=5.0
    )
    assert find_task(client, member4)["status"] == STATUS_QUEUED  # 会员槽仍满

    wait_downloads_settle(release)
