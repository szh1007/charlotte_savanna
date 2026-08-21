"""诊断探针: 对比三种 cookie 附加方式的 yt-dlp 内部请求 (排查字幕/下载鉴权问题用)."""

from __future__ import annotations

import http.cookiejar
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config
from yt_dlp import YoutubeDL
from yt_dlp.extractor.bilibili import BilibiliBaseIE

BVID = "BV1DC411J7Wy"
AID = 1953946374
CID = 1524209082
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


class DebugIE(BilibiliBaseIE):
    def _download_json(self, url, video_id, *args, **kwargs):
        print(f"  GET {url[:110]}")
        try:
            resp = super()._download_json(url, video_id, *args, **kwargs)
            if "x/player/wbi/v2" in url:
                data = (resp or {}).get("data") or {}
                subs = [
                    (s.get("lan"), s.get("subtitle_url", "")[:40])
                    for s in (data.get("subtitle") or {}).get("subtitles") or []
                ]
                print(
                    f"    -> code={resp.get('code')} login_mid={data.get('login_mid')} "
                    f"need_login_subtitle={data.get('need_login_subtitle')} subs={subs}"
                )
            return resp
        except Exception as e:
            print(f"    -> FAIL {type(e).__name__}: {str(e)[:160]}")
            raise


def write_netscape_cookie_file(cookie: str, path: str) -> None:
    """BILI_COOKIE 字符串 → Netscape cookie 文件 (yt-dlp cookiefile 格式)."""
    lines = ["# Netscape HTTP Cookie File"]
    for part in cookie.split(";"):
        key, sep, value = part.strip().partition("=")
        if not sep or not key:
            continue
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{key}\t{value}")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def run(label: str, extra_opts: dict) -> None:
    print(f"=== {label} ===")
    opts = {"quiet": True, "no_warnings": True}
    opts.update(extra_opts)
    ydl = YoutubeDL(opts)
    ie = DebugIE(ydl)
    try:
        result = ie._get_subtitles(BVID, CID, aid=AID)
        keys = [k for k in result if k != "danmaku"]
        print(f"  _get_subtitles 非弹幕轨: {keys}")
    except Exception as e:
        print(f"  _get_subtitles 异常: {type(e).__name__}: {str(e)[:160]}")
    print()


def main() -> None:
    run(
        "http_header (当前 subtitle.py)",
        {
            "http_headers": {
                "Referer": "https://www.bilibili.com/",
                "User-Agent": UA,
                "Cookie": config.BILI_COOKIE,
            }
        },
    )
    run("cookiejar 参数 (yt-dlp 会忽略)", {"cookiejar": http.cookiejar.CookieJar()})
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        write_netscape_cookie_file(config.BILI_COOKIE, f.name)
        cookiefile = f.name
    run("cookiefile (官方 Netscape 格式)", {"cookiefile": cookiefile})


if __name__ == "__main__":
    main()
