"""CharPlot 游戏化与统计服务层 (Issue 02).

规则参数集中配置 (DESIGN.md §5); 日期一律用 timezone.localdate() 保证
Asia/Shanghai 自然日语义 (USE_TZ=True). 所有函数支持 today 参数注入,
便于测试免 mock 时钟.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import CharplotUserEvent

# ---- 规则参数 (集中配置, 后续可改, 勿散落各处) ----
FREEZE_COIN_COST = 10  # 兑换 1 天连胜冻结所需学习币
FREEZE_DAYS = 1  # 每次兑换冻结天数


class StreakFreezeError(Exception):
    """连胜冻结业务异常基类."""


class InsufficientCoinsError(StreakFreezeError):
    """学习币不足, 无法兑换冻结."""


def record_event(user, event_type, event_date=None, payload=None):
    """记录用户事件, 同 (user, event_type, event_date) 按日去重 (get_or_create).

    登录事件按自然日去重, 重复登录不重复计行; 未来事件 (通关/答题) 同日
    多次可共存, 不设唯一约束.
    """
    return CharplotUserEvent.objects.get_or_create(
        user=user,
        event_type=event_type,
        event_date=event_date or timezone.localdate(),
        defaults={"payload": payload or {}},
    )[0]


def count_login_days(user):
    """登录天数 = LOGIN 事件按 event_date 去重计数."""
    return (
        CharplotUserEvent.objects.filter(
            user=user, event_type=CharplotUserEvent.EventType.LOGIN
        )
        .values("event_date")
        .distinct()
        .count()
    )


def buy_streak_freeze(profile, today=None):
    """学习币兑换连胜冻结 (DESIGN §5): 扣币 + 冻结顺延, 可叠加.

    冻结未过期则从现有 freeze_until 顺延, 已过期则从今天起算, 防止越兑越短.
    币不足抛 InsufficientCoinsError.
    """
    today = today or timezone.localdate()
    if profile.coins < FREEZE_COIN_COST:
        raise InsufficientCoinsError(
            f"学习币不足: 需 {FREEZE_COIN_COST} 币, 当前 {profile.coins} 币"
        )
    base = max(profile.freeze_until or today, today)
    with transaction.atomic():
        profile.coins -= FREEZE_COIN_COST
        profile.freeze_until = base + timedelta(days=FREEZE_DAYS)
        profile.save(update_fields=["coins", "freeze_until", "updated_at"])
    return profile


def get_streak_loss_warning(profile, today=None):
    """连胜中断检查: 最后学习日距今 > 1 天且未在冻结期内 → 警告.

    返回固定三字段结构, 前端统一消费:
    {warning, missed_days, freeze_until}
    """
    today = today or timezone.localdate()
    last = profile.last_study_date
    if last is None:
        # 从未学习 → 无警告
        return {
            "warning": False,
            "missed_days": 0,
            "freeze_until": profile.freeze_until,
        }
    if profile.freeze_until and today <= profile.freeze_until:
        # 冻结期内豁免中断检查
        return {
            "warning": False,
            "missed_days": 0,
            "freeze_until": profile.freeze_until,
        }
    missed_days = (today - last).days - 1  # 完整没学的天数
    if missed_days > 0:
        return {
            "warning": True,
            "missed_days": missed_days,
            "freeze_until": profile.freeze_until,
        }
    return {"warning": False, "missed_days": 0, "freeze_until": profile.freeze_until}


def settle_streak_on_login(profile, today=None):
    """登录时惰性连胜归零判定: 断连才动, 不学习不动.

    last_study_date == today → 今天已学习, 学习结算 (Issue 05) 已处理, 跳过;
    冻结保护期内豁免归零; 间隔 > 1 天且冻结已过期 → streak 归零 (max_streak
    保留历史峰值). 判定纯读状态且幂等, 未学习日重复登录重复执行无副作用.
    """
    today = today or timezone.localdate()
    last = profile.last_study_date
    if last is None or last == today:
        return profile  # 从未学习 / 今天已结算
    if profile.freeze_until and today <= profile.freeze_until:
        return profile  # 冻结保护期 (含当日) 豁免
    if (today - last).days > 1 and profile.streak != 0:
        profile.streak = 0
        profile.save(update_fields=["streak", "updated_at"])
    return profile


def build_profile_stats(user):
    """统计面板 (DESIGN §4.1): 登录天数现算, 答题/通关类字段由后续 issue 流入."""
    return {
        "login_days": count_login_days(user),
        "answered": 0,  # Issue 05: attempt 表聚合
        "correct": 0,  # Issue 05
        "wrong": 0,  # Issue 05
        "cleared_levels": 0,  # Issue 05: user_event(level_clear) 聚合
    }
