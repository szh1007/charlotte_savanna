"""T02 下载 + 交付直链链路验收测试 (HTTP seam, 引擎 mock)."""

import time

from backend import task_manager as tm
from backend.task_manager import STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED
from fastapi.testclient import TestClient
from helpers import create_download, find_task, wait_until
from yt_dlp.utils import DownloadError


def test_create_download_returns_queued_task(
    client: TestClient, fake_extract, fake_download
) -> None:
    """创建下载任务: 200 返回 task_id, 任务进入 queued 并最终 completed."""
    task_id = create_download(client, "https://www.bilibili.com/video/av1", "22")
    assert task_id > 0

    task = tm.manager.get_task(task_id)
    assert task is not None
    assert task.kind == "download"
    assert task.format_id == "22"
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)


def test_create_download_invalid_format_returns_400(
    client: TestClient, fake_extract, fake_download
) -> None:
    """无效档位: 400 + 明确错误, 任务标记 failed."""
    resp = client.post(
        "/api/downloads",
        json={"url": "https://www.bilibili.com/video/av1", "format_id": "9999"},
    )
    assert resp.status_code == 400
    assert "无效档位" in resp.json()["detail"]

    task = tm.manager.list_tasks()[0]
    assert task.status == STATUS_FAILED
    assert "9999" in task.error


def test_create_download_resolve_failure_returns_400(
    client: TestClient, monkeypatch
) -> None:
    """创建下载时解析失败: 400 + 引擎错误原因透传."""

    def _fail(url: str) -> dict:
        raise DownloadError("ERROR: Unsupported URL: https://example.com/v")

    monkeypatch.setattr("backend.downloader._extract", _fail)
    resp = client.post(
        "/api/downloads",
        json={"url": "https://example.com/v", "format_id": "18"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unsupported URL: https://example.com/v"

    task = tm.manager.list_tasks()[0]
    assert task.status == STATUS_FAILED


def test_download_flow_status_machine(
    client: TestClient, fake_extract: list[str], fake_download
) -> None:
    """完整流转: pending → resolving → resolved → queued → downloading → completed."""
    task_id = create_download(client, "https://www.bilibili.com/video/av2", "22")
    assert fake_extract == [tm.STATUS_RESOLVING]  # 解析期间为 resolving

    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)
    task = find_task(client, task_id)
    assert task["title"] == "测试视频标题"
    assert task["site"] == "BiliBili"
    assert task["progress"] == 100.0
    assert task["message"] == "下载完成"
    assert task["error"] is None


def test_download_failure_marks_task_failed(
    client: TestClient, fake_extract, monkeypatch
) -> None:
    """下载执行失败: 任务标记 failed 并携带引擎错误原因."""

    def _fail(url: str, format_id: str, out_dir: str, progress_hook=None) -> str:
        raise DownloadError("ERROR: The uploader has not made this video available")

    monkeypatch.setattr("backend.downloader._download", _fail)
    task_id = create_download(client, "https://www.bilibili.com/video/av3", "22")

    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_FAILED)
    task = find_task(client, task_id)
    assert task["error"] == "The uploader has not made this video available"


def test_download_unexpected_error_marks_failed(
    client: TestClient, fake_extract, monkeypatch
) -> None:
    """引擎外异常 (如磁盘错误): 任务标记 failed, 不悬挂在 downloading."""

    def _boom(url: str, format_id: str, out_dir: str, progress_hook=None) -> str:
        raise OSError("disk full")

    monkeypatch.setattr("backend.downloader._download", _boom)
    task_id = create_download(client, "https://www.bilibili.com/video/av11", "22")

    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_FAILED)
    task = find_task(client, task_id)
    assert "disk full" in task["error"]


def test_concurrent_downloads_use_single_slot(
    client: TestClient, fake_extract, fake_download
) -> None:
    """免费档 1 并发槽: 第一个下载阻塞期间, 同时下载的任务不超过 1 个."""
    _call_args, release = fake_download
    release.clear()
    id1 = create_download(client, "https://www.bilibili.com/video/av4", "22")
    id2 = create_download(client, "https://www.bilibili.com/video/av5", "18")

    # 等第一个任务进入 downloading 且进度已上报 (worker 已实际开始执行)
    assert wait_until(lambda: find_task(client, id1)["status"] == STATUS_DOWNLOADING)
    assert wait_until(lambda: find_task(client, id1)["progress"] == 50.0)
    # 阻塞期间反复采样: downloading 数恒为 1
    max_downloading = 0
    for _ in range(10):
        tasks = client.get("/api/tasks").json()["tasks"]
        n = sum(1 for t in tasks if t["status"] == STATUS_DOWNLOADING)
        max_downloading = max(max_downloading, n)
        if max_downloading > 1:
            break
        time.sleep(0.05)
    assert max_downloading == 1

    # 放行后两个任务都完成
    release.set()
    assert wait_until(
        lambda: (
            find_task(client, id1)["status"] == STATUS_COMPLETED
            and find_task(client, id2)["status"] == STATUS_COMPLETED
        )
    )


def test_tasks_desc_order_and_progress(
    client: TestClient, fake_extract, fake_download
) -> None:
    """任务列表按创建时间降序, 含进度与消息字段."""
    _call_args, release = fake_download
    release.clear()
    id1 = create_download(client, "https://www.bilibili.com/video/av6", "22")
    id2 = create_download(client, "https://www.bilibili.com/video/av7", "18")
    # 再创建一个解析任务 (kind=resolve, 不入队)
    rid = client.post(
        "/api/resolve", json={"url": "https://www.bilibili.com/video/av8"}
    ).json()["task_id"]

    assert wait_until(lambda: find_task(client, id1)["status"] == STATUS_DOWNLOADING)
    assert wait_until(lambda: find_task(client, id1)["progress"] == 50.0)
    tasks = client.get("/api/tasks").json()["tasks"]
    assert [t["task_id"] for t in tasks] == [rid, id2, id1]  # 降序

    # 下载阻塞期间 progress 与 message 已由进度 hook 更新
    task = find_task(client, id1)
    assert task["progress"] == 50.0
    assert task["message"] == "下载中 50%"
    assert task["kind"] == "download"
    assert task["formats"][0]["label"] == "360p MP4"

    release.set()
    assert wait_until(lambda: find_task(client, id1)["status"] == STATUS_COMPLETED)


def test_files_returns_attachment(
    client: TestClient, fake_extract, fake_download
) -> None:
    """completed 任务: 直链返回文件流 + Content-Disposition: attachment."""
    task_id = create_download(client, "https://www.bilibili.com/video/av9", "22")
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)

    resp = client.get(f"/api/files/{task_id}")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert "output.mp4" in resp.headers["content-disposition"]
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"fake-video-content"


def test_files_not_ready_returns_404(
    client: TestClient, fake_extract, fake_download
) -> None:
    """任务未完成时直链返回 404, 完成后可下载."""
    _call_args, release = fake_download
    release.clear()
    task_id = create_download(client, "https://www.bilibili.com/video/av10", "22")
    assert wait_until(
        lambda: find_task(client, task_id)["status"] == STATUS_DOWNLOADING
    )

    assert client.get(f"/api/files/{task_id}").status_code == 404

    release.set()
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)
    assert client.get(f"/api/files/{task_id}").status_code == 200
