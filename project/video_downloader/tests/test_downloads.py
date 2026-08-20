"""T02 下载 + 交付直链链路验收测试 (HTTP seam, 引擎 mock)."""

import time

import pytest
from backend import task_manager as tm
from backend.task_manager import STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED
from fastapi.testclient import TestClient
from helpers import create_download, find_task, member_headers, wait_until
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
        raise DownloadError("ERROR: Unable to download webpage: timeout")

    monkeypatch.setattr("backend.downloader._extract", _fail)
    resp = client.post(
        "/api/downloads",
        json={"url": "https://www.bilibili.com/video/av-fail", "format_id": "18"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unable to download webpage: timeout"

    task = tm.manager.list_tasks()[0]
    assert task.status == STATUS_FAILED


def test_create_download_rejects_non_bilibili_domain_422(
    client: TestClient,
) -> None:
    """创建下载时非哔哩哔哩域名: 前置域名白名单拒绝 (422), 不触达引擎 (ADR-0004)."""
    resp = client.post(
        "/api/downloads",
        json={"url": "https://www.youtube.com/watch?v=test", "format_id": "18"},
    )
    assert resp.status_code == 422
    assert "仅支持哔哩哔哩" in resp.json()["detail"]


def test_best_quality_download_uses_real_format_id(
    client: TestClient, fake_download, monkeypatch
) -> None:
    """B 站全 DASH 分离流: 「最佳画质」档位独立 id "best", 下载链路可用.

    回归 bugfix/0003: 字面 "best" 是 yt-dlp 格式选择表达式, 只匹配合一格式,
    全分离流下匹配为空下载报 "Requested format is not available". 独立 id
    "best" 记录用户选择, 下载时后端映射为 real_format_id (真实最高档 id).
    """
    dash_info = {
        "id": "dash-only",
        "title": "DASH 分离流视频",
        "thumbnail": "https://example.com/c.jpg",
        "extractor_key": "BiliBili",
        "formats": [
            # B 站真实结构: 纯音频流 + DASH video-only 视频流, 无合一格式
            {"format_id": "30216", "vcodec": "none", "acodec": "mp4a", "ext": "m4a"},
            {
                "format_id": "30016",
                "height": 360,
                "vcodec": "avc1",
                "acodec": "none",
                "ext": "mp4",
            },
            {
                "format_id": "30064",
                "height": 720,
                "vcodec": "avc1",
                "acodec": "none",
                "ext": "mp4",
            },
            {
                "format_id": "30080",
                "height": 1080,
                "vcodec": "avc1",
                "acodec": "none",
                "ext": "mp4",
            },
        ],
    }
    monkeypatch.setattr("backend.downloader._extract", lambda url: dash_info)

    # 解析 → 取「最佳画质」档位 (列表末尾, 独立 id "best")
    resp = client.post(
        "/api/resolve", json={"url": "https://www.bilibili.com/video/BV-best"}
    )
    formats = resp.json()["formats"]
    assert formats[-1]["label"] == "最佳画质 - 1080p"
    best_id = formats[-1]["format_id"]
    assert best_id == "best"  # 独立 id, 区分普通最高档 (real_format_id 不外传)
    # DASH video-only 档位标记无音频 → 下载时合并音频流 (bugfix/0003)
    assert formats[-1]["has_audio"] is False

    # 选最佳画质创建下载 → 引擎收到真实 id + merge_audio=True, 任务完成
    task_id = client.post(
        "/api/downloads",
        json={"url": "https://www.bilibili.com/video/BV-best", "format_id": best_id},
        headers=member_headers(client),
    ).json()["task_id"]
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)
    call_args, _release = fake_download
    assert call_args[-1][1] == "30080"
    assert call_args[-1][3] is True  # DASH 分离流: 合并音频流


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

    def _fail(
        url: str, format_id: str, out_dir: str, progress_hook=None, merge_audio=False
    ) -> str:
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

    def _boom(
        url: str, format_id: str, out_dir: str, progress_hook=None, merge_audio=False
    ) -> str:
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


def test_task_detail_returns_task(
    client: TestClient, fake_extract, fake_download
) -> None:
    """单任务详情: 200 返回完整任务, 不存在的任务 404 (契约 PRD §8)."""
    task_id = create_download(client, "https://www.bilibili.com/video/av12", "22")
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)

    resp = client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["task_id"] == task_id
    assert detail["status"] == STATUS_COMPLETED
    assert detail["title"] == "测试视频标题"
    assert detail["kind"] == "download"
    assert detail["progress"] == 100.0

    assert client.get("/api/tasks/99999").status_code == 404


def test_files_returns_attachment(
    client: TestClient, fake_extract, fake_download
) -> None:
    """completed 任务: 直链返回文件流 + Content-Disposition: attachment."""
    task_id = create_download(client, "https://www.bilibili.com/video/av9", "22")
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)

    resp = client.get(f"/api/files/{task_id}")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert "22.mp4" in resp.headers["content-disposition"]  # 文件名派生自 format_id
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


def test_merged_download_progress_averaged_across_streams(
    client: TestClient, monkeypatch, tmp_path
) -> None:
    """合并下载 (DASH 档位): 视频/音频流进度均分合成整体, 单调不减不回退.

    回归 bugfix/0004: yt-dlp 对合并表达式先后下载视频流与音频流, 各自独立
    上报进度; 修复前音频流会把整体进度重置回 0, 前端进度条来回跳.
    """
    history: list[float] = []

    def _fake(
        url: str, format_id: str, out_dir, progress_hook=None, merge_audio=False
    ) -> str:
        path = tmp_path / "merged.mp4"
        path.write_bytes(b"fake-video")
        # 模拟 yt-dlp 双流进度: 视频流 0→100%, 音频流 0→100% (各 3 帧)
        for fmt_id, pcts in (("30064", (10, 50, 100)), ("30280", (10, 50, 100))):
            for pct in pcts:
                progress_hook(
                    {
                        "status": "downloading",
                        "downloaded_bytes": pct,
                        "total_bytes": 100,
                        "info_dict": {"format_id": fmt_id},
                    }
                )
                history.append(tm.manager.list_tasks()[0].progress)
        progress_hook({"status": "finished"})
        return str(path)

    dash_info = {
        "id": "dash-av14",
        "title": "DASH 分离流视频",
        "thumbnail": "https://example.com/c.jpg",
        "extractor_key": "BiliBili",
        "formats": [
            {"format_id": "30216", "vcodec": "none", "acodec": "mp4a", "ext": "m4a"},
            {
                "format_id": "30064",
                "height": 720,
                "vcodec": "avc1",
                "acodec": "none",
                "ext": "mp4",
            },
        ],
    }
    monkeypatch.setattr("backend.downloader._extract", lambda url: dash_info)
    monkeypatch.setattr("backend.downloader._download", _fake)
    # 30064 为 DASH video-only 档位 (720p, 免费可选, merge_audio=True)
    task_id = create_download(client, "https://www.bilibili.com/video/av14", "30064")
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)
    # 均分合成: 视频流 10/50/100% → 整体 5/25/50%, 音频流 10/50/100% → 55/75/99
    # (99 为下载中上限, 完成时置 100); 全程单调不减, 无回退跳变
    assert history == pytest.approx([5.0, 25.0, 50.0, 55.0, 75.0, 99.0])
