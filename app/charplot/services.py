"""CharPlot 游戏化 / 统计 / 旅程服务层 (Issue 02 / 03).

规则参数集中配置 (DESIGN.md §5); 日期一律用 timezone.localdate() 保证
Asia/Shanghai 自然日语义 (USE_TZ=True). 所有函数支持 today 参数注入,
便于测试免 mock 时钟.
"""

import os
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import (
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
    CharplotUserEvent,
)

# ---- 规则参数 (集中配置, 后续可改, 勿散落各处) ----
FREEZE_COIN_COST = 10  # 兑换 1 天连胜冻结所需学习币
FREEZE_DAYS = 1  # 每次兑换冻结天数

JOURNEY_TITLE_MAX = 40  # 列表卡展示标题截断长度
JOURNEY_GRAPH_VERSION = 1  # 图谱契约版本 (CONTRACT.md, 只增不改)


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
    """连胜中断检查: 上次学习日次日 0 点起全天警告, 次日仍不学则第三天中断.

    上次学习日 (last_study_date):
    - 当天 → 打卡成功, 无警告
    - 次日 (missed_days=1) → 警告: 今天不学, 明天连胜中断
    - 第三天起 (missed_days>1) → 已中断 (登录结算已归零), 警告持续到重新学习
    冻结期内豁免. 返回固定三字段结构, 前端统一消费:
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
    missed_days = (today - last).days  # 距上次学习的自然日数, 次日即 1
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


# ---------------------------------------------------------------------------
# 旅程 (Issue 03)
# ---------------------------------------------------------------------------


class JourneyGraphError(ValueError):
    """图谱契约校验失败 (中文 detail 透传给 FastAPI 落库调用方)."""


def derive_journey_title(input_type, content="", filename=""):
    """从输入推导旅程标题: text/link 取内容首行截断, file 取去扩展名文件名."""
    if input_type == CharplotJourney.InputType.FILE and filename:
        base = os.path.splitext(os.path.basename(filename))[0]
        return base[:JOURNEY_TITLE_MAX]
    title = (
        (content or "").strip().splitlines()[0]
        if (content or "").strip()
        else "未命名旅程"
    )
    return title[:JOURNEY_TITLE_MAX]


def create_journey(user, input_type, content, source_file=None):
    """创建旅程: status=generating, title 由输入推导, 源文件一并落库."""
    filename = source_file.name if source_file else ""
    journey = CharplotJourney.objects.create(
        user=user,
        input_type=input_type,
        content=content or "",
        source_file=source_file,
        title=derive_journey_title(input_type, content, filename),
        status=CharplotJourney.Status.GENERATING,
    )
    return journey


def validate_graph(graph):
    """图谱契约校验 (CONTRACT.md v1), 失败抛 JourneyGraphError.

    校验项: 顶层 version/title/chapters; 章节 ≥1 且 id/title 必填;
    每章知识点 ≥1 且 id/title 必填; 临时 id 全局唯一; prerequisites 引用
    的 id 必须是本 journey 内已定义的 kp 临时 id (允许跨章节).
    """
    if not isinstance(graph, dict):
        raise JourneyGraphError("图谱必须是 JSON 对象")
    if graph.get("version") != JOURNEY_GRAPH_VERSION:
        raise JourneyGraphError(f"不支持的图谱契约版本: {graph.get('version')!r}")
    if not graph.get("title"):
        raise JourneyGraphError("图谱缺少 title")
    chapters = graph.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise JourneyGraphError("图谱至少需要 1 个章节")

    kp_ids: set[str] = set()
    for chapter in chapters:
        if (
            not isinstance(chapter, dict)
            or not chapter.get("id")
            or not chapter.get("title")
        ):
            raise JourneyGraphError("章节必须包含 id 与 title")
        kps = chapter.get("knowledge_points")
        if not isinstance(kps, list) or not kps:
            raise JourneyGraphError(f"章节 {chapter['id']!r} 至少需要 1 个知识点")
        for kp in kps:
            if not isinstance(kp, dict) or not kp.get("id") or not kp.get("title"):
                raise JourneyGraphError("知识点必须包含 id 与 title")
            if kp["id"] in kp_ids:
                raise JourneyGraphError(f"知识点临时 id 重复: {kp['id']!r}")
            kp_ids.add(kp["id"])

    for chapter in chapters:
        for kp in chapter["knowledge_points"]:
            for prereq in kp.get("prerequisites") or []:
                if prereq not in kp_ids:
                    raise JourneyGraphError(
                        f"知识点 {kp['id']!r} 引用了未知的前置知识点: {prereq!r}"
                    )


def save_journey_graph(journey, task_id, graph):
    """图谱落库 (CONTRACT.md): 先删后建, 事务内原子完成.

    幂等性: 重试只会发生在 failed 旅程 (无答题数据, Attempt/Level 是
    Issue 05 产物), 删除重建不产生重复行; 若未来对 ready 旅程重生成
    (涉及保留 error_score), 届时再评估更新策略.
    """
    validate_graph(graph)
    with transaction.atomic():
        journey.chapters.all().delete()  # 级联删除知识点与 M2M 依赖边
        kp_pk_by_tmp_id: dict[str, CharplotKnowledgePoint] = {}
        for order, chapter_data in enumerate(graph["chapters"]):
            chapter = CharplotChapter.objects.create(
                journey=journey,
                title=chapter_data["title"],
                summary=chapter_data.get("summary", ""),
                order=order,
            )
            for kp_order, kp_data in enumerate(chapter_data["knowledge_points"]):
                kp = CharplotKnowledgePoint.objects.create(
                    chapter=chapter,
                    title=kp_data["title"],
                    summary=kp_data.get("summary", ""),
                    order=kp_order,
                )
                kp_pk_by_tmp_id[kp_data["id"]] = kp
        for chapter_data in graph["chapters"]:
            for kp_data in chapter_data["knowledge_points"]:
                kp = kp_pk_by_tmp_id[kp_data["id"]]
                prereqs = [
                    kp_pk_by_tmp_id[p] for p in (kp_data.get("prerequisites") or [])
                ]
                kp.prerequisites.add(*prereqs)
        journey.graph = graph
        journey.latest_task_id = task_id
        journey.status = CharplotJourney.Status.READY
        journey.error_message = ""
        journey.save(
            update_fields=[
                "graph",
                "latest_task_id",
                "status",
                "error_message",
                "updated_at",
            ]
        )


def mark_journey_failed(journey, task_id, error_message):
    """任务失败标记: status=failed + 失败原因 (FastAPI 经内部端点调用)."""
    journey.status = CharplotJourney.Status.FAILED
    journey.latest_task_id = task_id
    journey.error_message = error_message[:1000]
    journey.save(
        update_fields=["status", "latest_task_id", "error_message", "updated_at"]
    )


# ---------------------------------------------------------------------------
# 技能树 (Issue 04)
# ---------------------------------------------------------------------------

# 技能树节点状态枚举 (skill-tree 接口输出, 前端 SkillNode 消费):
# locked=依赖未满足锁定 / unlocked=可解锁 / in_progress=进行中(Issue 05) /
# cleared=已通关点亮
SKILL_STATUS = ("locked", "unlocked", "in_progress", "cleared")


def _kp_status(prereq_ids, cleared_kp_ids):
    """知识点点亮状态 (PRD D-1): 依赖全部通关才解锁, 否则锁定.

    纯函数便于测试: 调用方把已通关知识点 id 集合 (Issue 05 由通关结算
    产出) 注入, 本期无关卡数据时传空集 → 有前置依赖的知识点一律锁定.
    """
    if prereq_ids and not set(prereq_ids) <= set(cleared_kp_ids):
        return "locked"
    return "unlocked"


def build_skill_tree(journey, cleared_kp_ids=None):
    """技能树图数据 (DESIGN §4.1, GET /api/charplot/journeys/{id}/skill-tree).

    返回 {nodes, edges}: nodes 为知识点节点 (章节归属 + 点亮状态 + 关卡进度
    合并字段), edges 为前置依赖边 (source → target, DAG). 前端据此渲染
    闯关地图 (vue-flow + dagre 布局).

    关卡进度字段 (cleared_levels/total_levels) 本期无 Level 数据恒为 0,
    节点进度徽章 "2/3" 由 Issue 05 创建关卡后按知识点聚合流入, 前端非零才显示.
    """
    cleared_kp_ids = cleared_kp_ids or set()
    nodes = []
    edges = []
    chapters = journey.chapters.prefetch_related("knowledge_points__prerequisites")
    for chapter in chapters.all():
        for kp in chapter.knowledge_points.all():
            prereq_ids = list(kp.prerequisites.values_list("id", flat=True))
            if kp.id in cleared_kp_ids:
                status = "cleared"
            else:
                status = _kp_status(prereq_ids, cleared_kp_ids)
            nodes.append(
                {
                    "id": kp.id,
                    "chapter_id": chapter.id,
                    "chapter_title": chapter.title,
                    "title": kp.title,
                    "order": kp.order,
                    "status": status,
                    "cleared_levels": 0,  # Issue 05: 该知识点已通关关卡数
                    "total_levels": 0,  # Issue 05: 该知识点关卡总数
                }
            )
            for pid in prereq_ids:
                edges.append({"id": f"e-{pid}-{kp.id}", "source": pid, "target": kp.id})
    return {"nodes": nodes, "edges": edges}
