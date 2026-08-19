"""pytest 共享 fixtures: TestClient + yt-dlp 引擎 mock.

测试 seam: HTTP API 层为主, 通过 TestClient 打 HTTP 断言行为,
引擎调用 (backend.downloader._extract) 被替换为伪数据, 不触网.
"""

from __future__ import annotations

import pytest
from backend import main
from backend import task_manager as tm
from fastapi.testclient import TestClient

# 伪解析结果 (模拟 yt-dlp extract_info 返回)
FAKE_INFO: dict = {
    "id": "test-video",
    "title": "测试视频标题",
    "thumbnail": "https://example.com/cover.jpg",
    "duration": 125.5,
    "extractor_key": "BiliBili",
    "formats": [
        # 360p 含音频 MP4
        {
            "format_id": "18",
            "height": 360,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
        },
        # 720p 含音频 MP4
        {
            "format_id": "22",
            "height": 720,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
        },
        # 1080p 无音频 MP4
        {
            "format_id": "137",
            "height": 1080,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "none",
        },
        # 1080p 含音频 WEBM (同高度应优先含音频)
        {
            "format_id": "999",
            "height": 1080,
            "ext": "webm",
            "vcodec": "vp9",
            "acodec": "opus",
        },
        # 纯音频流 (不构成档位, 应被跳过)
        {
            "format_id": "140",
            "height": None,
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a",
        },
    ],
}


@pytest.fixture(autouse=True)
def clean_tasks():
    """每个测试前清空任务存储, 保证断言基于干净状态."""
    tm.manager._tasks.clear()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def fake_extract(monkeypatch):
    """替换引擎调用点返回伪元信息, 并记录解析期间任务状态.

    返回 seen 列表: 解析执行中任务应处于 resolving 状态,
    用于断言 pending → resolving → resolved 的流转.
    """

    seen: list[str] = []

    def _fake_extract(url: str) -> dict:
        task = tm.manager.list_tasks()[0]
        seen.append(task.status)
        return FAKE_INFO

    monkeypatch.setattr("backend.downloader._extract", _fake_extract)
    return seen
