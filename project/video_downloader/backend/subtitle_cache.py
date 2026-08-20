"""模型字幕缓存 (ADR-0006): 转录段按 BV 号落盘, TTL 过期由 cleaner 清理.

缓存文件: SUBTITLES_DIR/<BV>.json; URL 带 p=N 且 N>1 时加 _pN 后缀
(分 P 隔离不串味). 内容 = 转录段 JSON + created_at + is_member
(过期判定按创建者身份 TTL, 与交付 TTL 同源: 免费 24h / 会员 72h).

全局共享命中: 任一用户转写过的视频, 其他人直接复用, 不另扣配额
(配额按任务计, 命中退还由 task_manager 处理). 官方字幕不缓存 (秒级获取).

BV 号解析失败 (b23.tv 短链 / av 号) 则本次跳过缓存 (不查不写), 不阻塞转录.
所有文件 IO 异常安全返回 miss (缓存是加速不是依赖, 失败不阻塞转写).
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger(__name__)


def _now() -> float:
    """可注入时钟: 测试推进时间验证缓存 TTL 过期 (与 cleaner._now 同模式)."""
    return time.time()


def _bvid(url: str) -> str | None:
    """从 B 站链接提取 BV 号; 短链 (b23.tv) / av 号链接不含 BV 返回 None."""
    m = re.search(r"BV[0-9A-Za-z]{10}", url or "")
    return m.group(0) if m else None


def cache_key(url: str) -> str | None:
    """缓存文件名 (不含扩展名): BV 号; URL 带 p=N 且 N>1 时 BV_pN.

    分 P 隔离: 多 P 视频每 P 的字幕独立缓存 (PRD US 75), 单 P (缺省)
    不加后缀. BV 号解析失败返回 None, 调用方跳过缓存 (不查不写).
    """
    bvid = _bvid(url)
    if not bvid:
        return None
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    p = (query.get("p") or [""])[0]
    if p.isdigit() and int(p) > 1:
        return f"{bvid}_p{p}"
    return bvid


def cache_path(url: str) -> Path | None:
    """缓存文件路径; BV 号解析失败返回 None (调用方跳过缓存)."""
    key = cache_key(url)
    return config.SUBTITLES_DIR / f"{key}.json" if key else None


def _is_expired(created_at: float, is_member: bool) -> bool:
    """过期判定 = now - created_at >= delivery_ttl(is_member) (与交付同源)."""
    return _now() - created_at >= config.delivery_ttl(is_member)


def get(url: str) -> list[dict[str, Any]] | None:
    """读缓存命中 → 转录段列表; 未命中 / 过期 / 损坏 / 无 BV 返回 None.

    过期缓存按 miss 处理 (由 cleaner 周期清理, 读取不主动删避免锁竞争);
    文件损坏 / 解码失败记录日志并返回 None, 不阻塞转录 (缓存是加速).
    """
    path = cache_path(url)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        segments = data.get("segments")
        if not isinstance(segments, list) or not segments:
            return None
        if _is_expired(data.get("created_at", 0), data.get("is_member", False)):
            return None
        return segments
    except (OSError, ValueError, TypeError) as e:
        logger.warning("字幕缓存读取失败 %s: %s (按未命中处理)", path, e)
        return None


def put(url: str, segments: list[dict[str, Any]], is_member: bool) -> Path | None:
    """写缓存: {segments, created_at, is_member}; 无 BV 号跳过返回 None.

    缓存按创建者身份 TTL 记录 (免费 24h / 会员 72h), cleaner 按文件内
    身份判定过期. 写入失败 (磁盘/权限) 仅记录日志, 不阻塞转录.
    """
    path = cache_path(url)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "segments": segments,
                    "created_at": _now(),
                    "is_member": is_member,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path
    except OSError as e:
        logger.warning("字幕缓存写入失败 %s: %s", path, e)
        return None


def cleanup_expired() -> list[Path]:
    """清理过期字幕缓存 (cleaner 周期调用): 按文件内创建者身份 TTL 删除.

    幂等: 已过期文件重复扫描无副作用; 无 BV 解析需求, 直接扫描目录内
    *.json. 模型本体不在本目录, 天然不清理 (持久资产).
    """
    removed: list[Path] = []
    try:
        files = list(config.SUBTITLES_DIR.glob("*.json"))
    except OSError:
        return removed  # 目录不存在等: 无缓存可清理
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if _is_expired(data.get("created_at", 0), data.get("is_member", False)):
                path.unlink(missing_ok=True)
                removed.append(path)
        except (OSError, ValueError, TypeError):
            # 损坏文件视为已过期 (无法判定身份的缓存无保留价值)
            try:
                path.unlink(missing_ok=True)
                removed.append(path)
            except OSError as e:
                logger.warning("字幕缓存清理失败 %s: %s", path, e)
    return removed
