"""T01 POST /api/resolve 解析链路验收测试."""

from backend import task_manager as tm
from backend.task_manager import STATUS_FAILED, STATUS_RESOLVED, STATUS_RESOLVING
from fastapi.testclient import TestClient
from yt_dlp.utils import DownloadError


def test_resolve_valid_link_returns_metadata(
    client: TestClient, fake_extract: list[str]
) -> None:
    resp = client.post(
        "/api/resolve", json={"url": "https://www.bilibili.com/video/av123"}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["task_id"] == 1
    assert body["status"] == "resolved"
    assert body["title"] == "测试视频标题"
    assert body["cover"] == "https://example.com/cover.jpg"
    assert body["duration"] == 125.5
    assert body["site"] == "BiliBili"

    # 档位列表: 360p / 720p / 1080p (同高度优先含音频 → WEBM);
    # 「最佳画质」伪档一律不展示 (与真实最高档重复, 用户反馈, 免费/会员同规则)
    formats = body["formats"]
    assert [f["label"] for f in formats] == [
        "360p MP4",
        "720p MP4",
        "1080p WEBM",
    ]
    # 免费用户: >720p 档位标记 locked, member_limited 为 True (T05)
    assert formats[0] == {
        "format_id": "18",
        "height": 360,
        "ext": "mp4",
        "label": "360p MP4",
        "locked": False,
        "has_audio": True,  # 合一格式 (含音频, 下载无需合并)
        "filesize": 1048576,  # 引擎提供文件大小 (1 MB)
    }
    assert formats[2]["format_id"] == "999"  # 1080p 含音频的 WEBM 胜出
    assert formats[2]["locked"] is True


def test_resolve_status_flows_pending_resolving_resolved(
    client: TestClient, fake_extract: list[str]
) -> None:
    """状态机流转: 解析期间为 resolving, 完成后为 resolved."""
    resp = client.post(
        "/api/resolve", json={"url": "https://www.bilibili.com/video/state-flow"}
    )
    assert resp.status_code == 200
    # 引擎执行时任务应处于 resolving 状态
    assert fake_extract == [STATUS_RESOLVING]
    # 请求返回后任务落定 resolved
    task = tm.manager.get_task(resp.json()["task_id"])
    assert task is not None
    assert task.status == STATUS_RESOLVED
    assert task.title == "测试视频标题"
    assert task.site == "BiliBili"


def test_resolve_invalid_url_returns_422(client: TestClient) -> None:
    """非 http(s) 链接: 前置校验直接拒绝."""
    resp = client.post("/api/resolve", json={"url": "not-a-url"})
    assert resp.status_code == 422
    assert "http" in resp.json()["detail"]


def test_resolve_rejects_non_bilibili_domain_422(client: TestClient) -> None:
    """非哔哩哔哩域名: 前置域名白名单直接拒绝 (422), 不触达引擎 (ADR-0004)."""
    resp = client.post("/api/resolve", json={"url": "https://example.com/v"})
    assert resp.status_code == 422
    assert "仅支持哔哩哔哩" in resp.json()["detail"]


def test_resolve_failure_marks_task_failed(client: TestClient, monkeypatch) -> None:
    """解析失败: 任务标记 failed 并记录错误原因."""

    def _fail(url: str) -> dict:
        raise DownloadError("ERROR: Unable to download webpage: timeout")

    monkeypatch.setattr("backend.downloader._extract", _fail)
    resp = client.post(
        "/api/resolve", json={"url": "https://www.bilibili.com/video/timeout"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unable to download webpage: timeout"

    task = tm.manager.list_tasks()[0]
    assert task.status == STATUS_FAILED
    assert task.error == "Unable to download webpage: timeout"


def test_resolve_upgrades_http_cover_to_https(client: TestClient, monkeypatch) -> None:
    """引擎返回 http 缩略图: 响应 cover 升级为 https (混合内容策略兼容)."""

    def _http_cover(url: str) -> dict:
        return {
            "title": "测试标题",
            "thumbnail": "http://i1.hdslb.com/bfs/archive/x.jpg",
            "extractor_key": "BiliBili",
            "formats": [],
        }

    monkeypatch.setattr("backend.downloader._extract", _http_cover)
    resp = client.post("/api/resolve", json={"url": "https://www.bilibili.com/video/x"})
    assert resp.status_code == 200
    assert resp.json()["cover"] == "https://i1.hdslb.com/bfs/archive/x.jpg"
