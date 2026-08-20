"""yt-dlp 引擎封装: 集中所有 yt_dlp 调用, 解析/下载结果可 mock (ADR-0001).

领域边界: 只下载不破解, 引擎能力即为领域能力边界.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.extractor import gen_extractor_classes
from yt_dlp.utils import DownloadError


class ResolveError(Exception):
    """解析失败 (不支持的平台 / 链接无效 / 网络错误)."""


def _clean_description(info: dict[str, Any]) -> str | None:
    """简介清洗: 引擎占位值 '-' / 空串视为无简介.

    B 站部分视频简介为空, yt-dlp 归一化为 '-' 占位, 直接透传会让
    前端显示无意义的单字符 (与 task_manager 口径一致).
    """
    desc = (info.get("description") or "").strip()
    if desc in ("", "-"):
        return None
    return desc[:500]


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
                # 是否含音频: 合一格式 True; DASH 分离流 (B 站等) video-only
                # False → 下载时需合并音频流才有声音 (见 _format_spec)
                "has_audio": f.get("acodec") not in (None, "none"),
                # 文件大小 (字节): 引擎多数档位提供, 缺失时前端显示「-」
                "filesize": f.get("filesize") or f.get("filesize_approx"),
            }
        )
    if formats:
        best = formats[-1]
        # 最佳画质用独立 format_id "best" 区分普通最高档 (用户反馈: 下载记录里
        # 「1080p」与「最佳画质 - 1080p」重复且无法追溯选择); 实际下载用
        # real_format_id (真实最高档 id): 字面 "best" 是 yt-dlp 格式选择表达式,
        # B 站等平台返回全 DASH 分离流时匹配为空 → 下载报
        # "Requested format is not available" (见 bugfix/0003).
        # real_format_id 为内部字段, FormatOut 序列化时被过滤, 前端仅按
        # format_id="best" 判定
        formats.append(
            {
                "format_id": "best",
                "real_format_id": best["format_id"],
                "height": best["height"],
                "ext": "mp4",
                "label": f"最佳画质 - {best['height']}p",
                "has_audio": best["has_audio"],
                "filesize": best["filesize"],
            }
        )
    return formats


def _cover_url(info: dict[str, Any]) -> str | None:
    """取封面 URL: 优先顶层 thumbnail, 缺失时回退 thumbnails 列表首个.

    国内平台 (B 站等) 缩略图常返回 http:// 链接, 统一升级为 https://:
    https 页面加载 http 图片会被浏览器混合内容策略 (Mixed Content) 拦截,
    封面显示失败. 主流平台图床均支持 https, 升级不会引入新的失败.
    """
    raw = info.get("thumbnail")
    if not raw:
        thumbnails = info.get("thumbnails") or []
        if thumbnails and thumbnails[0].get("url"):
            raw = thumbnails[0]["url"]
    if not raw:
        return None
    if raw.startswith("http://"):
        raw = "https://" + raw[len("http://") :]
    return raw


def resolve(url: str) -> dict[str, Any]:
    """解析链接, 返回元信息与可用档位列表; 失败抛 ResolveError."""
    try:
        info = _extract(url)
    except DownloadError as e:
        # 失败原因以引擎异常为准透传 (ADR-0001), 不自行猜测
        raise ResolveError(_friendly_message(e)) from e
    return {
        "title": info.get("title") or "未知标题",
        "cover": _cover_url(info),
        "duration": info.get("duration"),
        "site": info.get("extractor_key"),
        # 视频元信息 (前端卡片展示): up主 / 播放量 / 简介 (截断防大字段)
        "uploader": info.get("uploader"),
        "view_count": info.get("view_count"),
        # 占位值清洗: B 站空简介被 yt-dlp 归一化为 "-", 视为无简介
        # (与 task_manager._clean_description 口径一致)
        "description": _clean_description(info),
        "formats": _to_formats(info),
    }


def _friendly_message(e: DownloadError, fallback: str = "解析失败") -> str:
    """去掉引擎异常的 ERROR: 前缀, 保留可读原因."""
    msg = str(e)
    if msg.startswith("ERROR: "):
        msg = msg[len("ERROR: ") :]
    return msg or fallback


def _format_spec(format_id: str, merge_audio: bool) -> str:
    """构造传给 yt-dlp 的 format 参数.

    merge_audio=True (所选档位为 DASH 分离视频流, 无音频): 指定视频流 +
    最佳音频流合并, 输出有声文件; 斜杠后为回退 (平台无音频流时退回单流,
    避免整个格式选择失败). 合一格式档位不合并, 保持原样.
    """
    if merge_audio:
        return f"{format_id}+bestaudio*/{format_id}"
    return format_id


def _has_ffmpeg() -> bool:
    """yt-dlp 音视频合并依赖 ffmpeg 可执行文件 (PATH 或 FFMPEG_LOCATION)."""
    return shutil.which("ffmpeg") is not None


def _download(
    url: str,
    format_id: str,
    out_dir: Path,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    merge_audio: bool = False,
) -> str:
    """调用 yt-dlp 下载视频到 out_dir, 返回最终文件路径.

    独立的引擎调用点: 测试通过替换本函数 mock 下载过程.
    merge_audio=True 时视频流与最佳音频流合并 (需 ffmpeg),
    merge_output_format=mp4 保证输出单一 MP4 文件.
    """
    if merge_audio and not _has_ffmpeg():
        # 预先检测而非等 yt-dlp 下载完再失败: 提示安装, 避免浪费流量
        raise DownloadError(
            "该档位为音视频分离流, 需要 ffmpeg 合并: 请安装 ffmpeg 并加入 PATH"
        )
    opts: dict[str, Any] = {
        "format": _format_spec(format_id, merge_audio),
        "merge_output_format": "mp4",
        # 文件名: BV号_清晰度_视频流ID_音频流ID (用户反馈). requested_formats.N
        # 为 DASH 分离流 (视频流在前音频流在后) 的格式 id; 单流复合格式 (低清
        # 自带音频) 无独立音频 id, 渲染为 NA 占位 (缺失键默认值, 可接受)
        "outtmpl": str(
            out_dir / "%(bvid)s_%(height)sp_%(requested_formats.0.format_id)s_"
            "%(requested_formats.1.format_id)s.%(ext)s"
        ),
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
    merge_audio: bool = False,
) -> str:
    """下载视频为单一 MP4 文件, 返回文件路径; 失败抛 DownloadError (原因透传)."""
    try:
        return _download(url, format_id, out_dir, progress_hook, merge_audio)
    except DownloadError as e:
        raise DownloadError(_friendly_message(e, fallback="下载失败")) from e


# 主流平台展示清单 (名称 + 图标 + 支持格式 + yt-dlp extractor key)
# 范围收缩 (ADR-0004): 仅保留哔哩哔哩一项. 其他平台为预留扩展点,
# 未来如需恢复, 在此追加条目并同步放行 schemas.ensure_bilibili_url
# 的域名白名单 (接口只接受 B 站 URL, 平台墙数据同步由后端收窄)
# formats 为平台墙营销标注 (T09), 静态维护, 不随引擎动态探测
POPULAR_SITES: list[dict[str, str]] = [
    {"name": "B 站", "icon": "🅱️", "formats": "MP4 / FLV / 4K", "extractor": "BiliBili"},
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
        {"name": s["name"], "icon": s["icon"], "formats": s["formats"]}
        for s in POPULAR_SITES
        if s["extractor"] in supported
    ]
    return sites, len(supported)
