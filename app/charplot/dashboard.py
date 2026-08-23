"""CharPlot 分析 Dashboard 聚合 (Issue 12, SPEC §10).

掌握度矩阵 / 学习活动统计 / 易错点清单均从事实表 (charplot_attempt +
charplot_user_event) 按需聚合, 无额外埋点 (DESIGN.md 步骤 12 验证项:
数字与 Attempt / 事件一致). 日期语义与 services.py 一致 (timezone.localdate).

易错分优先级公式与 services._review_candidates 同源 (间隔复习同算法),
仅查询范围不同: 复习候选按旅程, 弱项清单全局聚合.
"""

import math
from datetime import timedelta

from django.db.models import Count, PositiveIntegerField, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    CharplotAttempt,
    CharplotKnowledgePoint,
    CharplotProfile,
    CharplotUserEvent,
)
from .services import REVIEW_NEVER_DAYS

WEAK_ACCURACY = 60  # 掌握度正确率 < 60% 判定为薄弱点 (前端高亮, PRD F-1)
ACTIVITY_DAYS = 14  # 活动趋势图时间窗口 (近 N 天, 含今日)


def _kp_attempt_rows(user):
    """逐题事实按知识点归属聚合 (answered / correct / duration).

    知识点归属与易错分锚点一致 (services.submit_answer 同款):
    question.source_kp or level.knowledge_point — 复习题计入来源知识点,
    保证掌握度与易错分语义闭环.
    """
    return (
        CharplotAttempt.objects.filter(user=user)
        .annotate(
            kp_id=Coalesce(
                "question__source_kp_id",
                "level__knowledge_point_id",
                output_field=PositiveIntegerField(),
            )
        )
        .values("kp_id")
        .annotate(
            answered=Count("id"),
            correct=Count("id", filter=Q(is_correct=True)),
            duration=Sum("duration"),
        )
    )


def build_mastery_matrix(user):
    """掌握度矩阵 (PRD F-1): 按旅程 → 章节 → 知识点聚合正确率.

    仅返回有答题记录的知识点 (未练习不占位); 章节统计 = 章内知识点
    Attempt 汇总; 薄弱点 (正确率 < WEAK_ACCURACY) 标记 weak=True.
    旅程按 -created_at, 章节 / 知识点按 order 排序.
    """
    rows = {r["kp_id"]: r for r in _kp_attempt_rows(user) if r["kp_id"]}
    if not rows:
        return {"journeys": []}

    kps = CharplotKnowledgePoint.objects.filter(id__in=rows.keys()).select_related(
        "chapter__journey"
    )
    journeys = {}
    for kp in kps:
        chapter = kp.chapter
        journey = chapter.journey
        journeys.setdefault(journey.id, {"journey": journey, "chapters": {}})
        journeys[journey.id]["chapters"].setdefault(
            chapter.id, {"chapter": chapter, "kps": []}
        )["kps"].append(kp)

    result = []
    for journey_id, group in sorted(
        journeys.items(), key=lambda item: item[1]["journey"].created_at, reverse=True
    ):
        chapters = []
        for chapter_id, cg in sorted(
            group["chapters"].items(), key=lambda item: item[1]["chapter"].order
        ):
            points = []
            for kp in sorted(cg["kps"], key=lambda k: (k.order, k.id)):
                r = rows[kp.id]
                correct = r["correct"]
                answered = r["answered"]
                accuracy = round(correct * 100 / answered) if answered else 0
                points.append(
                    {
                        "kp_id": kp.id,
                        "title": kp.title,
                        "order": kp.order,
                        "answered": answered,
                        "correct": correct,
                        "accuracy": accuracy,
                        "duration": r["duration"] or 0,
                        "error_score": kp.error_score,
                        "weak": accuracy < WEAK_ACCURACY,
                    }
                )
            answered = sum(p["answered"] for p in points)
            correct = sum(p["correct"] for p in points)
            chapters.append(
                {
                    "chapter_id": chapter_id,
                    "title": cg["chapter"].title,
                    "order": cg["chapter"].order,
                    "answered": answered,
                    "correct": correct,
                    "accuracy": round(correct * 100 / answered) if answered else 0,
                    "duration": sum(p["duration"] for p in points),
                    "knowledge_points": points,
                }
            )
        result.append(
            {
                "journey_id": journey_id,
                "title": group["journey"].title,
                "cleared": group["journey"].cleared,
                "chapters": chapters,
            }
        )
    return {"journeys": result}


def build_activity_stats(user, days=ACTIVITY_DAYS, today=None):
    """学习活动统计 (PRD F-2): 时长 / 通关数 / 活跃天数 / 连胜 + 近 N 天分布.

    - duration_seconds: Attempt.duration 求和 (作答耗时事实)
    - cleared_levels: LEVEL_CLEAR 事件计数 (与 build_profile_stats 同口径)
    - active_days: LOGIN 事件按日去重 (与个人主页登录天数一致)
    - streak / max_streak: profile 当前状态
    - daily: 近 N 天 (含今日) 每日答题 / 通关事件聚合, 无学习行为的天
      active=False, 缺日补零保证时间轴连续
    """
    today = today or timezone.localdate()
    profile, _ = CharplotProfile.objects.get_or_create(user=user)

    duration = (
        CharplotAttempt.objects.filter(user=user).aggregate(total=Sum("duration"))[
            "total"
        ]
        or 0
    )
    cleared = CharplotUserEvent.objects.filter(
        user=user, event_type=CharplotUserEvent.EventType.LEVEL_CLEAR
    ).count()
    active_days = (
        CharplotUserEvent.objects.filter(
            user=user, event_type=CharplotUserEvent.EventType.LOGIN
        )
        .values("event_date")
        .distinct()
        .count()
    )

    since = today - timedelta(days=days - 1)
    daily_rows = (
        CharplotUserEvent.objects.filter(
            user=user,
            event_type__in=[
                CharplotUserEvent.EventType.ANSWER,
                CharplotUserEvent.EventType.LEVEL_CLEAR,
            ],
            event_date__gte=since,
        )
        .values("event_date", "event_type")
        .annotate(count=Count("id"))
    )
    daily = {
        (today - timedelta(days=offset)).isoformat(): {"answers": 0, "cleared": 0}
        for offset in range(days - 1, -1, -1)
    }
    for row in daily_rows:
        key = row["event_date"].isoformat()
        if key in daily:
            if row["event_type"] == CharplotUserEvent.EventType.ANSWER:
                daily[key]["answers"] += row["count"]
            elif row["event_type"] == CharplotUserEvent.EventType.LEVEL_CLEAR:
                daily[key]["cleared"] += row["count"]

    return {
        "duration_seconds": duration,
        "cleared_levels": cleared,
        "active_days": active_days,
        "streak": profile.streak,
        "max_streak": profile.max_streak,
        "daily": [
            {
                "date": date_key,
                "answers": v["answers"],
                "cleared": v["cleared"],
                "active": v["answers"] + v["cleared"] > 0,
            }
            for date_key, v in daily.items()
        ],
    }


def build_weakpoint_list(user, today=None):
    """易错点清单 (PRD F-3): 全局 error_score>0 知识点按复习优先级排序.

    优先级公式与间隔复习同源 (services._review_candidates):
    priority = error_score * (距最近复习天数 + 1), 从未复习按
    REVIEW_NEVER_DAYS 天计; 排序 priority 降序 → error_score 降序 → id 升序
    (确定性 tie-break). priority_level 按清单内排名分三等份 (高/中/低),
    供前端标签展示. wrong_count = 该知识点名下答错 Attempt 数
    (含复习题按来源归属, 与 _kp_attempt_rows 一致).
    """
    today = today or timezone.localdate()
    ranked = []
    # 按用户隔离: kp 挂 journey.user, 全局查询会泄露他人易错数据
    for kp in CharplotKnowledgePoint.objects.filter(
        error_score__gt=0, chapter__journey__user=user
    ).select_related("chapter__journey"):
        # localdate() 而非 .date(): aware datetime 的 .date() 返回 UTC 日期,
        # 本地凌晨 (UTC 前一天) 会偏移 1 天 (services._review_candidates 同源
        # 公式, 此处为修正版, 不扩散既有缺陷)
        days = (
            (today - timezone.localdate(kp.last_reviewed_at)).days
            if kp.last_reviewed_at
            else REVIEW_NEVER_DAYS
        )
        ranked.append((kp.error_score * (days + 1), kp, days))
    ranked.sort(key=lambda item: (-item[0], -item[1].error_score, item[1].id))

    kp_ids = [kp.id for _, kp, _ in ranked]
    wrong_counts = (
        dict(
            _kp_attempt_rows(user)
            .filter(is_correct=False, kp_id__in=kp_ids)
            .values("kp_id")
            .annotate(wrong=Count("id"))
            .values_list("kp_id", "wrong")
        )
        if kp_ids
        else {}
    )

    n = len(ranked)
    third = max(1, math.ceil(n / 3))
    weakpoints = []
    for idx, (priority, kp, days) in enumerate(ranked):
        if idx < third:
            level = "high"
        elif idx < third * 2:
            level = "medium"
        else:
            level = "low"
        weakpoints.append(
            {
                "kp_id": kp.id,
                "title": kp.title,
                "journey_id": kp.chapter.journey_id,
                "journey_title": kp.chapter.journey.title,
                "chapter_title": kp.chapter.title,
                "error_score": kp.error_score,
                "days_since_review": days,
                "priority": priority,
                "priority_level": level,
                "wrong_count": wrong_counts.get(kp.id, 0),
            }
        )
    return {"weakpoints": weakpoints}
