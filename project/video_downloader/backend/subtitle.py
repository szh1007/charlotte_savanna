"""字幕快路径 (ADR-0005): yt-dlp + 服务端自备 cookie 提取 B 站官方字幕.

独立引擎调用点 (测试 mock 目标): 提取字幕映射 → 下载字幕内容 → 解析为
统一的 [{start, end, text}] 时间戳文本结构 (Transcript 原料).

B 站 AI 字幕为 JSON (body: [{from, to, content}]), CC 字幕为 vtt/srt 文本.
字幕接口要求 Referer 校验, 统一附加 B 站 Referer 头.
任何一步失败 / 无字幕返回 None, 由调用方回退 SenseVoice 转写 (ADR-0005).
"""

from __future__ import annotations

import http.cookiejar
import json
import logging
import re
import time
import urllib.request
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.cookies import YoutubeDLCookieJar

from . import config

logger = logging.getLogger(__name__)

# 中文字幕优先顺序 (B 站 AI 字幕 / CC 字幕 lang 键)
_ZH_LANGS = ("zh-CN", "zh-Hans", "zh", "ai-zh")

# 字幕提取重试 (用户反馈: B 站 API 偶发 502 Bad Gateway, 属临时故障,
# 重试退避后通常可恢复; 连续失败才回退 ASR, 减少不必要的转写开销)
_EXTRACT_RETRIES = 3
_EXTRACT_RETRY_BASE_SLEEP = 2.0  # 重试间隔基数 (秒), 逐次翻倍

_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}


def _parse_cookiejar(cookie: str, domain: str = ".bilibili.com") -> YoutubeDLCookieJar:
    """Cookie 字符串 ("; " 分隔 k=v) → yt-dlp CookieJar, 内存态不落盘.

    yt-dlp 的 cookiejar 只从 cookiefile / cookiesfrombrowser 加载, 忽略
    cookiejar 参数; http_headers 塞 Cookie 也会被限定到下载 URL 域
    (www.bilibili.com, yt-dlp 的 _apply_header_cookies 机制), 字幕接口在
    api.bilibili.com 域收不到, 导致配了 cookie 仍匿名. 因此用 yt-dlp 的
    YoutubeDLCookieJar 显式建域 (.bilibili.com 覆盖 www/api 子域), 在
    首次访问前赋值覆盖 cached_property, 敏感 cookie 不写磁盘.
    """
    jar = YoutubeDLCookieJar()
    for part in cookie.split(";"):
        key, sep, value = part.strip().partition("=")
        if not sep or not key:
            continue
        jar.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=key,
                value=value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=False,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )
    return jar


def _extract_subtitles(url: str) -> dict[str, Any] | None:
    """调用 yt-dlp 提取字幕映射 {lang: [caption dicts]}; 失败/无字幕返回 None.

    独立的引擎调用点: 测试通过替换本函数 mock 字幕提取结果.
    cookie 仅在配置时附加: 未配置 BILI_COOKIE 时引擎走匿名提取, 大概率
    无字幕 (B 站字幕需登录), 快速失败回退 ASR, 不浪费请求.
    """
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "http_headers": dict(_HEADERS),
        # yt-dlp 基类 extract_subtitles 需该参数才触发提取, 否则静默返回空
        "writesubtitles": True,
    }
    last_error: Exception | None = None
    for attempt in range(_EXTRACT_RETRIES):
        try:
            with YoutubeDL(opts) as ydl:
                if config.BILI_COOKIE:
                    # 内存注入登录态: 首次访问前赋值覆盖 cached_property
                    #  (见 _parse_cookiejar)
                    ydl.cookiejar = _parse_cookiejar(config.BILI_COOKIE)
                info = ydl.extract_info(url, download=False)
            break
        except Exception as e:
            last_error = e
            if attempt < _EXTRACT_RETRIES - 1:
                # 退避重试 (2s → 4s): B 站 API 临时 502 重试可恢复
                time.sleep(_EXTRACT_RETRY_BASE_SLEEP * (2**attempt))
    else:
        logger.warning("字幕提取失败 %s: %s (回退 ASR)", url, last_error)
        return None
    subs = info.get("subtitles") or {}
    for lang in _ZH_LANGS:
        if subs.get(lang):
            return subs[lang]
    for captions in subs.values():  # 任选首个可用语言
        if captions:
            return captions
    return None


def _fetch_caption(url: str) -> str:
    """下载字幕文件内容 (B 站字幕接口需 Referer).

    yt-dlp 返回的 subtitle_url 为协议相对 URL (//aisubtitle.hdslb.com/...),
    urllib 不识别, 需补全 https 前缀.
    """
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_caption(content: str) -> list[dict[str, Any]]:
    """按内容格式解析为 [{start, end, text}]: 优先 JSON (B 站 AI 字幕)."""
    stripped = content.strip()
    if stripped.startswith("{"):
        return _parse_json_body(stripped)
    if stripped.startswith("WEBVTT"):
        return _parse_vtt(stripped)
    return _parse_srt(stripped)


def _parse_json_body(content: str) -> list[dict[str, Any]]:
    """B 站 AI 字幕 JSON: {body: [{from, to, content}]}."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return _parse_srt(content)  # 不是 JSON 就按字幕文本兜底解析
    segments = []
    for item in data.get("body") or []:
        text = (item.get("content") or "").strip()
        if not text:
            continue
        segments.append(
            {
                "start": float(item.get("from", 0)),
                "end": float(item.get("to", 0)),
                "text": text,
            }
        )
    return segments


def _parse_vtt(content: str) -> list[dict[str, Any]]:
    """WEBVTT: 时间行 (HH:MM:SS.mmm --> ...) + 文本行."""
    segments = []
    cue_times = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2}\.?\d*)\s*-->\s*(\d{1,2}):(\d{2}):(\d{2}\.?\d*)"
    )
    start = end = None
    for line in content.splitlines():
        m = cue_times.search(line)
        if m:
            start = _to_seconds(*m.groups()[:3])
            end = _to_seconds(*m.groups()[3:])
            continue
        if start is not None and line.strip() and "-->" not in line:
            text = line.strip()
            segments.append({"start": start, "end": end or start, "text": text})
            start = end = None  # 一条字幕一行 (B 站 vtt 单行文本)
    return segments


def _parse_srt(content: str) -> list[dict[str, Any]]:
    """SRT: 序号 + 时间行 (HH:MM:SS,mmm --> ...) + 文本 (可多行)."""
    segments = []
    cue_times = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2}),(\d{3})"
    )
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        m = cue_times.search(lines[i])
        if not m:
            i += 1
            continue
        start = _to_seconds(*m.groups()[:3])
        end = _to_seconds(*m.groups()[4:7])
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
            text_lines.append(lines[i].strip())
            i += 1
        if text_lines:
            segments.append({"start": start, "end": end, "text": " ".join(text_lines)})
    return segments


def _to_seconds(h: str, m: str, s: str) -> float:
    """HH:MM:SS[.mmm] → 秒 (vtt/srt 时间行共用)."""
    return int(h) * 3600 + int(m) * 60 + float(s)


def get_subtitles(url: str) -> list[dict[str, Any]] | None:
    """获取字幕转录段; 无字幕 / 提取或解析失败返回 None (回退 ASR).

    只取首个可用中文字幕轨, 统一为 [{start, end, text}] 结构.
    """
    captions = _extract_subtitles(url)
    if not captions:
        return None
    caption = captions[0]
    # B 站 AI 字幕由 yt-dlp 提取时已内联 (data, srt 文本); 个别轨只有 url 再下载
    content = caption.get("data")
    if content is None:
        url_ = caption.get("url")
        if not url_:
            return None
        try:
            content = _fetch_caption(url_)
        except Exception as e:
            logger.warning("字幕内容下载失败: %s (回退 ASR)", e)
            return None
    segments = _parse_caption(content)
    return segments or None
