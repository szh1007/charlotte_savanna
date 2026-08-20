"""免费档每日配额 (ADR-0005): 按匿名 client_id + 日窗口计数, 内存态.

免费用户无会话 token (会员密钥体系), 前端在 localStorage 生成持久化
client_id (UUID), 随请求以 X-Client-Id header 携带, 作为免费身份计数键.
会员 (有效 X-Member-Token) 不受配额限制, 不进入本模块计数.
计数内存态 (与 ADR-0003 一致), 服务重启清零; 过期日窗口惰性清理.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from . import config

# 配额类型: summary = 视频总结 / qa = AI 问答 (上限从 config 读取)
SUMMARY = "summary"
QA = "qa"

# 类型 → (计数字段名, config 上限变量名, 显示名): 收敛 kind 分发, 单一来源
_KINDS: dict[str, tuple[str, str, str]] = {
    SUMMARY: ("summary_count", "FREE_SUMMARY_DAILY", "视频总结"),
    QA: ("qa_count", "FREE_QA_DAILY", "AI 问答"),
}

# 日窗口键格式: YYYY-MM-DD (本地时间; 学习项目不做时区配置, 与交付 TTL 一致)
_DAY_FMT = "%Y-%m-%d"


def _spec(kind: str) -> tuple[str, str, str]:
    """按类型取 (计数字段, 上限变量, 显示名); 未知类型明确报错而非静默计数."""
    try:
        return _KINDS[kind]
    except KeyError:
        raise ValueError(f"未知配额类型: {kind}") from None


class QuotaExceededError(Exception):
    """当日配额已用尽 (路由层转为 429 + 明确提示)."""


@dataclass
class DailyUsage:
    """单客户端单日的用量计数 (两类配额独立计数)."""

    day: str
    summary_count: int = 0
    qa_count: int = 0


def _today() -> str:
    """可注入时钟的当日键 (测试推进日期验证配额重置)."""
    return time.strftime(_DAY_FMT)


class QuotaManager:
    """每日配额存储 (线程安全): 按 client_id 计日用量 + 惰性过期清理."""

    def __init__(self) -> None:
        self._usages: dict[str, DailyUsage] = {}
        self._lock = threading.Lock()
        self._pruned_day: str | None = None  # 上次已回收的日窗口 (跨日时全量清理)

    def _prune(self, today: str) -> None:
        """回收非今日的过期条目: 每日首次调用时全量扫描一次, 防无界增长."""
        if today == self._pruned_day:
            return
        self._usages = {k: v for k, v in self._usages.items() if v.day == today}
        self._pruned_day = today

    def _get(self, client_id: str) -> DailyUsage:
        """取当日用量记录; 跨日 / 新客户端自动重置为今日 (惰性清理)."""
        today = _today()
        self._prune(today)
        usage = self._usages.get(client_id)
        if usage is None or usage.day != today:
            usage = DailyUsage(day=today)
            self._usages[client_id] = usage
        return usage

    def check(self, client_id: str, kind: str) -> None:
        """检查配额是否可用, 超限抛 QuotaExceededError (计数在 use 时执行).

        缺省 client_id (前端未携带) 视为无效身份, 拒绝而非放行:
        免费配额必须可被强制, 防绕过 (与档位锁定同一纵深防御思路).
        """
        if not client_id:
            raise QuotaExceededError("缺少客户端标识 (X-Client-Id), 无法计费")
        with self._lock:
            usage = self._get(client_id)
            if self._limit(kind) <= self._count(usage, kind):
                raise QuotaExceededError(self._message(kind))

    def use(self, client_id: str, kind: str) -> None:
        """实际计一次用量 (配额检查通过后调用, 与 check 分离避免竞态窗口).

        check → use 之间有并发窗口: 单客户端重复提交可能短暂超限,
        学习项目可接受 (不追求强一致, 与内存态存储定位一致).
        """
        with self._lock:
            usage = self._get(client_id)
            field_name, _, _ = _spec(kind)
            setattr(usage, field_name, getattr(usage, field_name) + 1)

    @staticmethod
    def _limit(kind: str) -> int:
        """配额上限按类型从 config 读取 (免费 3 总结 / 10 问答, ADR-0005)."""
        _, var, _ = _spec(kind)
        return getattr(config, var)

    @staticmethod
    def _count(usage: DailyUsage, kind: str) -> int:
        field_name, _, _ = _spec(kind)
        return getattr(usage, field_name)

    @staticmethod
    def _message(kind: str) -> str:
        _, var, label = _spec(kind)
        return f"免费用户每日 {label} 限 {getattr(config, var)} 次, 已用完 (会员不限)"


# 模块级单例: 路由 / task_manager 共享
quota = QuotaManager()
