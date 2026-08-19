"""yt-dlp 引擎封装: 集中所有 yt_dlp 调用, 解析/下载结果可 mock (ADR-0001).

领域边界: 只下载不破解, 引擎能力即为领域能力边界.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.extractor import gen_extractor_classes
from yt_dlp.utils import DownloadError


class ResolveError(Exception):
    """解析失败 (不支持的平台 / 链接无效 / 网络错误)."""


def _extract(url: str) -> dict[str, Any]:
    """调用 yt-dlp 提取视频元信息 (只提取不下载).

    独立的引擎调用点: 测试通过替换本函数 mock 解析结果.
    """
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _to_formats(info: dict[str, Any]) -> list[dict[str, Any]]:
    """从 yt-dlp formats 列表转换为档位列表.

    规则: 仅保留视频流, 按高度去重 (同高度优先含音频的格式),
    末尾附「最佳画质」档位.
    """
    by_height: dict[int, dict[str, Any]] = {}
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none"):
            continue  # 纯音频流不构成档位
        height = f.get("height")
        if not height:
            continue
        cur = by_height.get(height)
        has_audio = f.get("acodec") not in (None, "none")
        cur_has_audio = cur is not None and cur.get("acodec") not in (None, "none")
        if cur is None or (has_audio and not cur_has_audio):
            by_height[height] = f

    formats: list[dict[str, Any]] = []
    for height in sorted(by_height):
        f = by_height[height]
        ext = f.get("ext") or "mp4"
        formats.append(
            {
                "format_id": str(f["format_id"]),
                "height": height,
                "ext": ext,
                "label": f"{height}p {ext.upper()}",
            }
        )
    if formats:
        best_height = formats[-1]["height"]
        formats.append(
            {
                "format_id": "best",
                "height": best_height,
                "ext": "mp4",
                "label": f"最佳画质 ({best_height}p)",
            }
        )
    return formats


def resolve(url: str) -> dict[str, Any]:
    """解析链接, 返回元信息与可用档位列表; 失败抛 ResolveError."""
    try:
        info = _extract(url)
    except DownloadError as e:
        # 失败原因以引擎异常为准透传 (ADR-0001), 不自行猜测
        raise ResolveError(_friendly_message(e)) from e
    return {
        "title": info.get("title") or "未知标题",
        "cover": info.get("thumbnail"),
        "duration": info.get("duration"),
        "site": info.get("extractor_key"),
        "formats": _to_formats(info),
    }


def _friendly_message(e: DownloadError, fallback: str = "解析失败") -> str:
    """去掉引擎异常的 ERROR: 前缀, 保留可读原因."""
    msg = str(e)
    if msg.startswith("ERROR: "):
        msg = msg[len("ERROR: ") :]
    return msg or fallback


def _download(
    url: str,
    format_id: str,
    out_dir: Path,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """调用 yt-dlp 下载视频到 out_dir, 返回最终文件路径.

    独立的引擎调用点: 测试通过替换本函数 mock 下载过程.
    merge_output_format=mp4 保证输出单一 MP4 文件 (音视频分离流由 ffmpeg 合并).
    """
    opts: dict[str, Any] = {
        "format": format_id,
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "%(id)s_%(format_id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    downloads_info = info.get("requested_downloads") or []
    if downloads_info and downloads_info[0].get("filepath"):
        return downloads_info[0]["filepath"]
    # 不带 ERROR: 前缀 (外层 download() 不再 strip 自有异常)
    raise DownloadError("下载完成但无法定位输出文件")


def download(
    url: str,
    format_id: str,
    out_dir: Path,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """下载视频为单一 MP4 文件, 返回文件路径; 失败抛 DownloadError (原因透传)."""
    try:
        return _download(url, format_id, out_dir, progress_hook)
    except DownloadError as e:
        raise DownloadError(_friendly_message(e, fallback="下载失败")) from e


# 主流平台展示清单 (名称 + 图标 + yt-dlp extractor key)
# key 以引擎实际 ie_key() 为准 (如 Youtube / Iqiyi / VQQVideo), 不支持的平台不展示
POPULAR_SITES: list[dict[str, str]] = [
    {"name": "B 站", "icon": "🅱️", "extractor": "BiliBili"},
    {"name": "抖音", "icon": "🎵", "extractor": "Douyin"},
    {"name": "YouTube", "icon": "▶️", "extractor": "Youtube"},
    {"name": "小红书", "icon": "📕", "extractor": "XiaoHongShu"},
    {"name": "微博", "icon": "📢", "extractor": "Weibo"},
    {"name": "腾讯视频", "icon": "📺", "extractor": "VQQVideo"},
    {"name": "优酷", "icon": "🌀", "extractor": "Youku"},
    {"name": "爱奇艺", "icon": "💜", "extractor": "Iqiyi"},
    {"name": "西瓜视频", "icon": "🍉", "extractor": "Ixigua"},
    {"name": "TikTok", "icon": "🎶", "extractor": "TikTok"},
    {"name": "Instagram", "icon": "📷", "extractor": "Instagram"},
    {"name": "X (Twitter)", "icon": "🐦", "extractor": "Twitter"},
]


@lru_cache(maxsize=1)
def _supported_keys() -> set[str]:
    """引擎当前支持的 extractor key 集合 (启动后缓存)."""
    # lazy extractor 无 IE_KEY 属性, 使用官方 ie_key() 类方法
    return {ie.ie_key() for ie in gen_extractor_classes()}


def list_sites() -> tuple[list[dict[str, str]], int]:
    """返回主流平台列表 (校验引擎支持性) 与引擎全量支持数."""
    supported = _supported_keys()
    sites = [
        {"name": s["name"], "icon": s["icon"]}
        for s in POPULAR_SITES
        if s["extractor"] in supported
    ]
    return sites, len(supported)
