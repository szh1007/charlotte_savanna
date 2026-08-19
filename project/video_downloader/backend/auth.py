"""会员会话管理: 密钥校验 + 内存态会话 token (24h TTL, ADR-0002).

提交正确 MEMBER_KEY 后签发随机 token, 后续请求通过 X-Member-Token header
被识别为会员; 会话内存态存储, 查询时惰性清理过期项 (量级极小, 无后台线程).
免费用户是合法身份: 无 token / token 无效 / 过期一律视为免费档, 不报错.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import Header

from . import config

# 会话有效期 (秒): 24h (PRD §6), 测试可缩短验证过期
MEMBER_SESSION_TTL = 24 * 60 * 60


def _now() -> float:
    """可注入时钟: 测试推进时间验证会话过期 (无全局副作用)."""
    return time.time()


@dataclass
class MemberSession:
    """一次会员会话: token + 过期时间."""

    token: str
    expires_at: float

    @property
    def is_valid(self) -> bool:
        return _now() < self.expires_at


class MemberManager:
    """会员会话存储 (线程安全): 密钥校验 / 会话查询 / 惰性过期清理."""

    def __init__(self) -> None:
        self._sessions: dict[str, MemberSession] = {}
        self._lock = threading.Lock()

    def verify_key(self, key: str) -> MemberSession | None:
        """校验密钥 (恒定时间比较防时序攻击), 通过则签发会话 token."""
        if not config.MEMBER_KEY:
            return None  # 未配置密钥: 拒绝一切提交
        # bytes 比较: compare_digest 对非 ASCII str 抛 TypeError,
        # 错误输入也须 401 而非 500
        if not hmac.compare_digest(
            key.encode("utf-8"), config.MEMBER_KEY.encode("utf-8")
        ):
            return None
        token = secrets.token_urlsafe(32)
        session = MemberSession(token=token, expires_at=_now() + MEMBER_SESSION_TTL)
        with self._lock:
            self._sessions[token] = session
        return session

    def get_session(self, token: str | None) -> MemberSession | None:
        """按 token 查询有效会话; 无 / 无效 / 过期返回 None (视为免费用户).

        过期项惰性删除: 会话收回后 token 立即失效, 不再占用存储.
        """
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if not session.is_valid:
                del self._sessions[token]
                return None
            return session


# 模块级单例: 路由与测试共享同一会话存储
member_manager = MemberManager()


def get_member(
    x_member_token: str | None = Header(default=None, alias="X-Member-Token"),
) -> MemberSession | None:
    """FastAPI 依赖: 从 X-Member-Token header 识别会员, 无 / 无效返回 None.

    供受保护逻辑 (T05 档位锁定 / 并发槽 / 队列上限) 注入使用.
    """
    return member_manager.get_session(x_member_token)
