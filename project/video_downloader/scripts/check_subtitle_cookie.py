"""验证脚本: 已配置 BILI_COOKIE 时能否提取到 B 站字幕 (ADR-0005 快路径回归).

真实网络调用, 不 mock. 用法:
    python scripts/check_subtitle_cookie.py [video_url ...]

走 subtitle.get_subtitles 真实路径 (含 cookie 注入), 输出字幕段数与
前几条内容, 判定 cookie 场景是否打通. 默认测试两个视频:
    - BV1GJ411x7h7: 旧 E2E 默认视频 (可能无字幕轨)
    - BV1DC411J7Wy: 已确认有 AI 字幕 (ai-zh)
cookie 值不回显 (敏感信息).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config
from backend.subtitle import get_subtitles

DEFAULT_URLS = [
    "https://www.bilibili.com/video/BV1GJ411x7h7",
    "https://www.bilibili.com/video/BV1DC411J7Wy",
]


def _fields(cookie: str) -> list[str]:
    return [
        p.strip().split("=", 1)[0] for p in cookie.split(";") if p.strip() and "=" in p
    ]


def main() -> None:
    urls = sys.argv[1:] or DEFAULT_URLS
    if config.BILI_COOKIE:
        names = _fields(config.BILI_COOKIE)
        print(
            f"BILI_COOKIE: 已配置 (长度 {len(config.BILI_COOKIE)}, "
            f"字段 {len(names)} 个, "
            f"SESSDATA: {'存在' if 'SESSDATA' in names else '缺失'})\n"
        )
    else:
        print("BILI_COOKIE: 未配置 (跳过登录态验证)\n")

    for url in urls:
        print(f"── {url} ──")
        segments = get_subtitles(url)
        if not segments:
            print("  结果: 无字幕 (视频无字幕轨或提取失败)")
        else:
            print(f"  结果: 提取到 {len(segments)} 条字幕段")
            for seg in segments[:3]:
                print(
                    f"    [{seg['start']:7.1f} → {seg['end']:7.1f}] {seg['text'][:60]}"
                )
        print()


if __name__ == "__main__":
    main()
