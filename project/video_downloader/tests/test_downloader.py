"""downloader 引擎封装纯函数单元测试 (cover 归一化 / 档位映射 / 合并格式)."""

import pytest
from backend.downloader import (
    _cover_url,
    _download,
    _format_spec,
    _has_ffmpeg,
    _to_formats,
)
from yt_dlp.utils import DownloadError


def test_cover_upgrades_http_to_https() -> None:
    """http 缩略图升级 https: https 页面加载 http 图片会被混合内容策略拦截."""
    info = {"thumbnail": "http://i1.hdslb.com/bfs/archive/x.jpg"}
    assert _cover_url(info) == "https://i1.hdslb.com/bfs/archive/x.jpg"


def test_cover_keeps_https_unchanged() -> None:
    """https 缩略图原样保留."""
    info = {"thumbnail": "https://example.com/cover.jpg"}
    assert _cover_url(info) == "https://example.com/cover.jpg"


def test_cover_falls_back_to_thumbnails_list() -> None:
    """顶层 thumbnail 缺失时回退 thumbnails 列表首个 (同样升级 https)."""
    info = {
        "thumbnails": [
            {"url": "http://img.example.com/a.jpg"},
            {"url": "https://img.example.com/b.jpg"},
        ]
    }
    assert _cover_url(info) == "https://img.example.com/a.jpg"


def test_cover_none_when_missing() -> None:
    """thumbnail 与 thumbnails 均缺失时返回 None."""
    assert _cover_url({}) is None


def test_to_formats_best_points_to_real_id_on_dash_only() -> None:
    """全 DASH 分离流 (B 站真实结构): 最佳画质指向最高档真实 id, 非字面 "best".

    字面 "best" 是 yt-dlp 格式选择表达式, 只匹配音视频合一格式; B 站返回
    全分离流时匹配为空, 下载报 "Requested format is not available" (bugfix/0003).
    """
    info = {
        "formats": [
            # 纯音频流 (不构成档位)
            {"format_id": "30216", "vcodec": "none", "acodec": "mp4a", "ext": "m4a"},
            # DASH 视频流 (video-only, 无音频)
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
        ]
    }
    formats = _to_formats(info)
    assert [f["format_id"] for f in formats] == ["30016", "30064", "30080", "30080"]
    assert formats[-1]["label"] == "最佳画质 - 1080p"
    assert formats[-1]["height"] == 1080
    # DASH video-only 档位 has_audio=False → 下载时合并音频流 (bugfix/0003)
    assert all(f["has_audio"] is False for f in formats)


def test_to_formats_marks_merged_formats_has_audio() -> None:
    """合一格式 (含音频): has_audio=True, 下载保持单流不合并."""
    info = {
        "formats": [
            {"format_id": "18", "height": 360, "vcodec": "avc1", "acodec": "mp4a"},
        ]
    }
    formats = _to_formats(info)
    assert formats[0]["has_audio"] is True
    assert formats[-1]["has_audio"] is True  # 最佳画质复制最高档标记


def test_format_spec_keeps_single_stream_when_has_audio() -> None:
    """合一档位: format 参数原样, 不引入合并."""
    assert _format_spec("18", merge_audio=False) == "18"


def test_format_spec_merges_audio_for_dash_stream() -> None:
    """DASH video-only 档位: 视频流 + 最佳音频流合并, 无音频平台回退单流."""
    assert _format_spec("30064", merge_audio=True) == "30064+bestaudio*/30064"


def test_download_requires_ffmpeg_when_merging(monkeypatch, tmp_path) -> None:
    """合并下载需 ffmpeg: 缺失时预先报明确错误, 不等到下载完成才失败."""
    monkeypatch.setattr("backend.downloader.shutil.which", lambda _: None)

    with pytest.raises(DownloadError, match="ffmpeg"):
        _download("https://example.com/v", "30064", tmp_path, merge_audio=True)


def test_has_ffmpeg_detects_path(monkeypatch) -> None:
    """ffmpeg 检测: PATH 中存在返回 True."""
    monkeypatch.setattr("backend.downloader.shutil.which", lambda _: "/usr/bin/ffmpeg")
    assert _has_ffmpeg() is True
