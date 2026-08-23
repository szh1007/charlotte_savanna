"""CharPlot 游戏化 / 统计 / 旅程 / 闯关服务层 (Issue 02 / 03 / 05 / 06).

规则参数集中配置 (DESIGN.md §5); 日期一律用 timezone.localdate() 保证
Asia/Shanghai 自然日语义 (USE_TZ=True). 所有函数支持 today 参数注入,
便于测试免 mock 时钟.
"""

import logging
import os
import secrets
import unicodedata
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import (
    CharplotAttempt,
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
    CharplotLevel,
    CharplotProfile,
    CharplotQuestion,
    CharplotReviewReport,
    CharplotUserEvent,
)

logger = logging.getLogger(__name__)

# ---- 规则参数 (集中配置, 后续可改, 勿散落各处) ----
FREEZE_COIN_COST = 10  # 兑换 1 天连胜冻结所需学习币
FREEZE_DAYS = 1  # 每次兑换冻结天数

JOURNEY_TITLE_MAX = 40  # 列表卡展示标题截断长度
JOURNEY_GRAPH_VERSION = 1  # 图谱契约版本 (CONTRACT.md, 只增不改)

# ---- 闯关 / 游戏化规则 (DESIGN §5, SPEC §9) ----
MAX_HEARTS = 5  # 每关心动值上限, 答错 -1, 扣完本关重开
ANSWER_CORRECT_XP = 10  # 答对即时 XP
LEVEL_CLEAR_XP = 50  # 通关奖励 XP
LEVEL_CLEAR_COINS = 15  # 通关奖励学习币
ERROR_SCORE_WRONG = 2  # 易错分: 答错 +2
ERROR_SCORE_RIGHT = -1  # 易错分: 答对 -1
LEVEL_QUESTION_MIN = 5  # 关卡题数范围 (PRD D-2)
LEVEL_QUESTION_MAX = 8
LEVEL_XP_THRESHOLDS = [0, 100, 250, 450, 700, 1000, 1400, 1800, 2300, 3000]
# 等级 = 满足的档位数, 如 xp=0 → 1 级, xp=100 → 2 级


class StreakFreezeError(Exception):
    """连胜冻结业务异常基类."""


class InsufficientCoinsError(StreakFreezeError):
    """学习币不足, 无法兑换冻结."""


def record_event(user, event_type, event_date=None, payload=None, dedupe=True):
    """记录用户事件.

    dedupe=True (默认): 同 (user, event_type, event_date) 按日去重 (登录事件).
    dedupe=False: 每次新增一行, 答题/通关等逐条事实事件同日多次共存.
    """
    event_date = event_date or timezone.localdate()
    if dedupe:
        return CharplotUserEvent.objects.get_or_create(
            user=user,
            event_type=event_type,
            event_date=event_date,
            defaults={"payload": payload or {}},
        )[0]
    return CharplotUserEvent.objects.create(
        user=user,
        event_type=event_type,
        event_date=event_date,
        payload=payload or {},
    )


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
    """统计面板 (DESIGN §4.1): 事实表聚合, 与 Attempt / 用户事件一致 (SPEC §8).

    登录天数 = LOGIN 事件按日去重; 答题对错 = attempt 逐条计数; 通关数 =
    LEVEL_CLEAR 事件计数 (同一关多次通关按事件行计, 与结算行为一致).
    """
    stats = {
        "login_days": count_login_days(user),
        "answered": CharplotAttempt.objects.filter(user=user).count(),
        "cleared_levels": CharplotUserEvent.objects.filter(
            user=user, event_type=CharplotUserEvent.EventType.LEVEL_CLEAR
        ).count(),
    }
    stats["correct"] = (
        CharplotAttempt.objects.filter(user=user, is_correct=True).count()
        if stats["answered"]
        else 0
    )
    stats["wrong"] = stats["answered"] - stats["correct"]
    return stats


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

    关卡数据从 journey.levels 聚合 (Issue 05): cleared_kp_ids 缺省时由已通关
    关卡推导, 进度徽章 cleared_levels/total_levels 按知识点合并; 某知识点
    有关卡进行中 (未通关但已作答/已扣心) 时节点状态为 in_progress.
    """
    # 按知识点聚合关卡统计, 一次查询避免 N+1
    level_stats: dict[int, dict] = {}
    for level in journey.levels.all():
        stat = level_stats.setdefault(
            level.knowledge_point_id, {"cleared": 0, "total": 0, "in_progress": False}
        )
        stat["total"] += 1
        if level.cleared:
            stat["cleared"] += 1
        elif level.current_index > 0 or level.hearts < MAX_HEARTS:
            stat["in_progress"] = True
    if cleared_kp_ids is None:
        cleared_kp_ids = {kp_id for kp_id, s in level_stats.items() if s["cleared"] > 0}

    nodes = []
    edges = []
    chapters = journey.chapters.prefetch_related("knowledge_points__prerequisites")
    for chapter in chapters.all():
        for kp in chapter.knowledge_points.all():
            prereq_ids = list(kp.prerequisites.values_list("id", flat=True))
            stat = level_stats.get(
                kp.id, {"cleared": 0, "total": 0, "in_progress": False}
            )
            if kp.id in cleared_kp_ids:
                status = "cleared"
            elif stat["in_progress"]:
                status = "in_progress"
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
                    "cleared_levels": stat["cleared"],
                    "total_levels": stat["total"],
                }
            )
            for pid in prereq_ids:
                edges.append({"id": f"e-{pid}-{kp.id}", "source": pid, "target": kp.id})
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# 闯关答题 (Issue 05)
# ---------------------------------------------------------------------------


class LevelError(ValueError):
    """关卡业务异常基类 (中文 detail 透传前端)."""


class LevelClearedError(LevelError):
    """关卡已通关, 不可继续答题."""


class LevelFailedError(LevelError):
    """心动值已扣完, 需重开后继续."""


class LevelNotCurrentError(LevelError):
    """提交的题目不是当前题 (防跳题/重放)."""


def level_status(level):
    """关卡状态 (API 输出): cleared / failed(心扣完) / in_progress / pending."""
    if level.cleared:
        return "cleared"
    if level.hearts <= 0:
        return "failed"
    if level.current_index > 0:
        return "in_progress"
    return "pending"


def normalize_answer(text):
    """填空归一化 (DESIGN §5 判分规则): NFKC 全角→半角 + 去空白 + 小写."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    return "".join(normalized.split()).lower()


# stub 题目干扰项兜底词 (章节/知识点不足时填充选项)
_STUB_DISTRACTORS = ("核心概念", "基础理论", "实践技巧")

# 关卡题数 (5-8 范围, PRD D-2)
_STUB_QUESTION_COUNT = 6


def _stub_questions(level, journey):
    """确定性 stub 题目 (Issue 05; Issue 08 真实生成替换).

    6 题 = 选择 2 + 判断 2 + 填空 1 + 判断 1 (简单收尾): 由浅入深
    (识别 → 回忆, PRD D-2), 填空中后段难度峰值, 简单判断题收尾保成功感.
    内容基于图谱内知识点/章节标题派生, 讲解自洽; sources 留空为来源引用位
    (Issue 08 填充). 同一旅程重复生成结果一致 (重开同题).
    """
    kp = level.knowledge_point
    chapter = kp.chapter
    # 干扰项候选: 其他知识点标题 + 兜底词, 与正确项去重后取 3 个
    others = [
        p.title
        for ch in journey.chapters.all()
        for p in ch.knowledge_points.all()
        if p.id != kp.id and p.title != kp.title
    ]
    chapter_titles = list(journey.chapters.values_list("title", flat=True))
    summary = kp.summary or f"本图谱解构出的核心知识点, 贯穿「{chapter.title}」章节."
    prereq_titles = list(kp.prerequisites.values_list("title", flat=True))

    def distractors(exclude, count=3):
        pool = [t for t in (others + list(_STUB_DISTRACTORS)) if t not in exclude]
        # 确定性去重保序, 不足时兜底词补齐
        seen, picks = set(), []
        for item in pool:
            if item not in seen:
                seen.add(item)
                picks.append(item)
            if len(picks) >= count:
                break
        return (picks + list(_STUB_DISTRACTORS))[:count]

    chapter_options = distractors(chapter.title)
    summary_options = distractors(summary)
    questions = [
        {
            "question_type": CharplotQuestion.QuestionType.CHOICE,
            "content": f"「{kp.title}」在本图谱中属于哪个章节?",
            "options": [chapter.title, *chapter_options],
            "answer": [0],
            "explanation": (
                f"「{kp.title}」属于「{chapter.title}」章节, 同章知识点共同"
                f"构成该部分的知识结构."
            ),
        },
        {
            "question_type": CharplotQuestion.QuestionType.CHOICE,
            "content": f"下列关于「{kp.title}」的概述, 最准确的是?",
            "options": [summary, *summary_options],
            "answer": [0],
            "explanation": f"「{kp.title}」的概述: {summary}",
        },
        {
            "question_type": CharplotQuestion.QuestionType.JUDGE,
            "content": f"「{kp.title}」是「{chapter.title}」章节的知识点.",
            "options": [],
            "answer": ["true"],
            "explanation": (
                f"「{kp.title}」是「{chapter.title}」的知识点之一, 该章共"
                f" {chapter.knowledge_points.count()} 个知识点."
            ),
        },
        {
            "question_type": CharplotQuestion.QuestionType.FILL,
            "content": (
                f"请补全知识点名称: 概述为「{summary[:40]}…」的知识点是 ____"
                if len(summary) > 40
                else f"请补全知识点名称: 概述为「{summary}」的知识点是 ____"
            ),
            "options": [],
            "answer": [kp.title],
            "explanation": f"正确答案: 「{kp.title}」. {summary}",
        },
        {
            "question_type": CharplotQuestion.QuestionType.JUDGE,
            "content": f"「{kp.title}」的前置知识点必须全部通关后, 本知识点才会解锁.",
            "options": [],
            "answer": ["true" if prereq_titles else "false"],
            "explanation": (
                f"「{kp.title}」的前置依赖: {'、'.join(prereq_titles) or '无'}. "
                f"技能树按前置依赖解锁, 依赖满足前节点锁定."
            ),
        },
        {
            "question_type": CharplotQuestion.QuestionType.JUDGE,
            "content": f"本图谱的所有知识点都属于「{chapter.title}」这一个章节.",
            "options": [],
            "answer": ["true" if len(chapter_titles) == 1 else "false"],
            "explanation": (
                f"本图谱共 {len(chapter_titles)} 个章节: "
                f"{'、'.join(chapter_titles)}. "
                f"「{kp.title}」属于「{chapter.title}」."
            ),
        },
    ]
    return questions


def ensure_levels_for_journey(journey):
    """为无关卡的知识点创建关卡 (stub 题目, Issue 05).

    幂等: 已有关卡的知识点跳过; 图谱重生成后新增的知识点自动补关卡.
    返回新创建的关卡列表.
    """
    existing = set(journey.levels.values_list("knowledge_point_id", flat=True))
    created = []
    for chapter in journey.chapters.prefetch_related("knowledge_points"):
        for kp in chapter.knowledge_points.all():
            if kp.id in existing:
                continue
            level = CharplotLevel.objects.create(journey=journey, knowledge_point=kp)
            for order, data in enumerate(_stub_questions(level, journey)):
                CharplotQuestion.objects.create(level=level, order=order, **data)
            created.append(level)
    return created


def check_answer(question, user_answer):
    """判分 (DESIGN §5): 选择/判断精确匹配, 填空归一化模糊匹配.

    user_answer 为 JSON 数组 (与 answer 同构): 选择 [下标 int], 判断
    ["true"/"false"], 填空 [原文 str]. 格式不匹配一律判错, 不抛异常.
    """
    if question.question_type == CharplotQuestion.QuestionType.CHOICE:
        try:
            return [int(user_answer[0])] == question.answer
        except (TypeError, ValueError, IndexError):
            return False
    if question.question_type == CharplotQuestion.QuestionType.JUDGE:
        return bool(user_answer) and str(user_answer[0]) == str(question.answer[0])
    if question.question_type == CharplotQuestion.QuestionType.FILL:
        if not user_answer:
            return False
        norm = normalize_answer(user_answer[0])
        return any(normalize_answer(a) == norm for a in question.answer)
    return False


def level_from_xp(xp):
    """等级 = 满足的 XP 档位数 (规则参数 LEVEL_XP_THRESHOLDS)."""
    return sum(1 for threshold in LEVEL_XP_THRESHOLDS if xp >= threshold)


def _update_streak_on_study(profile, today):
    """学习日连胜结算 (PRD G-2): 昨天学过 → +1; 断连或首次 → 1; 今日已结算 → 不变.

    max_streak 保留历史峰值; last_study_date 推进为今日 (登录惰性归零判定
    依赖此字段, 冻结期内学习同样推进).
    """
    last = profile.last_study_date
    if last == today:
        return
    if last == today - timedelta(days=1):
        profile.streak += 1
    else:
        profile.streak = 1
    profile.max_streak = max(profile.max_streak, profile.streak)
    profile.last_study_date = today


def _settle_level_clear(level, profile, today):
    """通关结算 (PRD D-5): XP + 学习币 + 连胜更新 + 事件落库 + 旅程点亮检查.

    旅程全部关卡通关后置 journey.cleared (CONTRACT.md §4); 返回 reward 载荷
    供 API 透传前端结算动画.
    """
    profile.xp += LEVEL_CLEAR_XP
    profile.coins += LEVEL_CLEAR_COINS
    _update_streak_on_study(profile, today)
    record_event(
        level.journey.user,
        CharplotUserEvent.EventType.LEVEL_CLEAR,
        today,
        {"level_id": level.id, "journey_id": level.journey_id},
        dedupe=False,
    )
    journey = level.journey
    # 排除当前关卡: 其 cleared=True 尚未落库 (结算在 save 前执行)
    if (
        not journey.cleared
        and not journey.levels.exclude(id=level.id).filter(cleared=False).exists()
    ):
        journey.cleared = True
        journey.save(update_fields=["cleared", "updated_at"])
        # 旅程全部通关 → 自动生成复盘报告 (Issue 06, 幂等, 与结算同事务)
        create_review_report(journey)
    return {
        "xp": LEVEL_CLEAR_XP,
        "coins": LEVEL_CLEAR_COINS,
        "streak": profile.streak,
        "max_streak": profile.max_streak,
        "level": level_from_xp(profile.xp),
        "journey_cleared": journey.cleared,
    }


def submit_answer(level, question_id, answer, duration=0, today=None):
    """提交答案: 判分 → Attempt/事件落库 → 心动值/XP/易错分 → 通关结算.

    防重放: question_id 必须属于本关且是当前题 (current_index 定位), 否则抛
    LevelNotCurrentError; 已通关抛 LevelClearedError; 心扣完抛 LevelFailedError.
    答错扣关卡心 (扣完即本关失败, 不结算), 答对即时 +XP; 答完最后一题且
    还有剩余心 → 通关结算. 返回结构化结果 (serializer 直出).
    """
    today = today or timezone.localdate()
    if level.cleared:
        raise LevelClearedError("本关已通关")
    if level.hearts <= 0:
        raise LevelFailedError("心动值已扣完, 请重开本关")
    question_count = level.questions.count()
    if level.current_index >= question_count:
        # 脏数据兜底: 进度越界且未通关 (题库变更等), 提示重开
        raise LevelError("关卡进度异常, 请重开后继续")
    current = level.questions.order_by("order", "id")[level.current_index]
    question = level.questions.filter(pk=question_id).first()
    if question is None or question.id != current.id:
        raise LevelNotCurrentError("题目序号不匹配, 请刷新后重试")

    user = level.journey.user
    profile, _ = CharplotProfile.objects.get_or_create(user=user)
    correct = check_answer(question, answer)
    cleared = False
    reward = None

    with transaction.atomic():
        CharplotAttempt.objects.create(
            user=user,
            level=level,
            question=question,
            is_correct=correct,
            user_answer=answer,
            duration=max(0, int(duration or 0)),
        )
        record_event(
            user,
            CharplotUserEvent.EventType.ANSWER,
            today,
            {
                "level_id": level.id,
                "question_id": question.id,
                "correct": correct,
            },
            dedupe=False,
        )
        kp = level.knowledge_point
        if correct:
            profile.xp += ANSWER_CORRECT_XP
            kp.error_score = max(0, kp.error_score + ERROR_SCORE_RIGHT)
        else:
            level.hearts = max(0, level.hearts - 1)
            profile.hearts = level.hearts  # 导航显示同步为关卡剩余心
            kp.error_score += ERROR_SCORE_WRONG
        level.current_index += 1
        if level.current_index >= question_count and level.hearts > 0:
            level.cleared = True
            cleared = True
            reward = _settle_level_clear(level, profile, today)
        profile.level = level_from_xp(profile.xp)

        kp.save(update_fields=["error_score"])
        level.save(update_fields=["hearts", "current_index", "cleared", "updated_at"])
        profile.save(
            update_fields=[
                "xp",
                "level",
                "hearts",
                "coins",
                "streak",
                "max_streak",
                "last_study_date",
                "updated_at",
            ]
        )

    return {
        "correct": correct,
        "explanation": question.explanation,
        "sources": question.sources,
        "hearts": level.hearts,
        "level_status": (
            "cleared" if cleared else ("failed" if level.hearts <= 0 else "in_progress")
        ),
        "cleared": cleared,
        "reward": reward,
        "progress": {
            "current_index": level.current_index,
            "question_count": question_count,
        },
    }


def restart_level(level):
    """重开关卡 (5 心扣完): 心与进度重置, 题目不变 (stub 确定性, 重开同题).

    Attempt 历史记录保留不覆盖 (掌握度分析的事实源, SPEC §8); profile.hearts
    同步重置回满.
    """
    user = level.journey.user
    with transaction.atomic():
        level.hearts = MAX_HEARTS
        level.current_index = 0
        level.cleared = False
        level.save(update_fields=["hearts", "current_index", "cleared", "updated_at"])
        CharplotProfile.objects.filter(user=user).update(hearts=MAX_HEARTS)
    return level


# ---------------------------------------------------------------------------
# 复盘报告 (Issue 06)
# ---------------------------------------------------------------------------

REPORT_SLUG_LENGTH = 12  # 公开短链长度
REPORT_SLUG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
# 去易混淆字符 (0/o/1/l/i), 便于口头传播
REPORT_TITLE_MAX = 40  # OG 标题截断
REPORT_DESC_MAX = 200  # OG 描述截断

# OG 社交卡片 (1200x630, 微信/QQ/推特通用)
_OG_IMAGE_WIDTH = 1200
_OG_IMAGE_HEIGHT = 630
_OG_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑 (自用 Windows 环境)
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
_OG_DIR = "app/charplot/uploads/og"  # MEDIA_ROOT 下相对目录


def generate_report_slug():
    """生成不可猜测的公开 slug (secrets 密码学随机), 撞库重试."""
    while True:
        slug = "".join(
            secrets.choice(REPORT_SLUG_ALPHABET) for _ in range(REPORT_SLUG_LENGTH)
        )
        if not CharplotReviewReport.objects.filter(slug=slug).exists():
            return slug


def build_report_stats(journey):
    """答题统计快照 (PRD E-1): 从 Attempt 聚合, 与事实表逐条一致 (SPEC §8).

    返回总答题数/对/错/正确率(整数百分比)/总耗时 + 每关明细; 关卡重开的
    历史 Attempt 一并计入 (与 profile 统计同源, 掌握度分析需要历史事实).
    """
    attempts = CharplotAttempt.objects.filter(level__journey=journey).select_related(
        "level__knowledge_point__chapter"
    )
    total = correct = duration = 0
    per_level: dict[int, dict] = {}
    for attempt in attempts:
        total += 1
        correct += 1 if attempt.is_correct else 0
        duration += attempt.duration
        level = attempt.level
        stat = per_level.setdefault(
            level.id,
            {
                "level_id": level.id,
                "kp_id": level.knowledge_point_id,
                "kp_title": level.knowledge_point.title,
                "chapter_title": level.knowledge_point.chapter.title,
                "answered": 0,
                "correct": 0,
            },
        )
        stat["answered"] += 1
        stat["correct"] += 1 if attempt.is_correct else 0
    return {
        "answered": total,
        "correct": correct,
        "wrong": total - correct,
        "accuracy": round(correct * 100 / total) if total else 0,
        "duration": duration,
        "levels": sorted(per_level.values(), key=lambda s: s["level_id"]),
    }


def build_knowledge_summary(journey):
    """知识总结 (PRD E-1): 章节 → 知识点 (标题 + 概述).

    stub 阶段为图谱确定性聚合, 与 JourneyDetail 图谱同源 (LLM 文字总结
    为 Issue 13, 接入后分享页同步增强, 快照结构不变).
    """
    chapters = []
    for chapter in journey.chapters.prefetch_related("knowledge_points").all():
        chapters.append(
            {
                "title": chapter.title,
                "summary": chapter.summary,
                "knowledge_points": [
                    {"title": kp.title, "summary": kp.summary}
                    for kp in chapter.knowledge_points.all()
                ],
            }
        )
    return {"chapters": chapters}


def _report_og_texts(journey, stats):
    """OG 标题/描述 (PRD E-2): 分享到社交平台卡片展示用."""
    og_title = f"{journey.title} · 通关复盘"[:REPORT_TITLE_MAX]
    kp_count = CharplotKnowledgePoint.objects.filter(chapter__journey=journey).count()
    og_description = (
        f"通关 {journey.title} 共 {stats['answered']} 道题, 答对 "
        f"{stats['correct']} 道 ({stats['accuracy']}%), 掌握 {kp_count} 个知识点."
    )[:REPORT_DESC_MAX]
    return og_title, og_description


def _load_og_font(size):
    """按候选顺序加载中文字体, 全缺失时回退 Pillow 默认 (豆腐块, 不崩溃)."""
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for path in _OG_FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _draw_wrapped_text(draw, text, font, x, y, fill, max_width, line_height):
    """按最大宽度逐行截断绘制 (中文字符按字宽估算, 简易换行)."""
    lines = []
    while text:
        # 二分找能容纳的最长前缀
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if draw.textlength(text[:mid], font=font) <= max_width:
                lo = mid
            else:
                hi = mid - 1
        if lo == 0:
            lo = 1  # 单字超宽兜底, 避免死循环
        lines.append(text[:lo])
        text = text[lo:]
    for i, line in enumerate(lines):
        draw.text((x, y + i * line_height), line, font=font, fill=fill)


def render_og_image(report, journey, stats):
    """Pillow 绘制 1200x630 社交卡片 PNG (B站粉渐变底 + 标题 + 统计摘要).

    输出 MEDIA_ROOT/{_OG_DIR}/{slug}.png, 返回相对 URL; 任何异常吞掉并记
    日志 (图缺失不阻塞报告生成与分享页, OG 卡片仍显示标题/摘要).
    """
    from django.conf import settings

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return ""
    try:
        # 粉色 → 浅紫竖向渐变 (B站粉主色 + 二次元柔和色系, DESIGN §6)
        img = Image.new("RGB", (_OG_IMAGE_WIDTH, _OG_IMAGE_HEIGHT))
        top = (251, 114, 153)
        bottom = (201, 182, 228)
        draw = ImageDraw.Draw(img)
        for y in range(_OG_IMAGE_HEIGHT):
            t = y / (_OG_IMAGE_HEIGHT - 1)
            color = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            draw.line([(0, y), (_OG_IMAGE_WIDTH, y)], fill=color)

        font_label = _load_og_font(44)
        font_title = _load_og_font(72)
        font_stats = _load_og_font(48)
        white = (255, 255, 255)

        draw.text((90, 90), "CHARPLOT · 通关复盘", font=font_label, fill=white)
        # 标题最多 2 行 (行高约字号 1.3 倍), 过长截断
        title = journey.title[:40]
        _draw_wrapped_text(
            draw,
            title,
            font_title,
            90,
            190,
            white,
            _OG_IMAGE_WIDTH - 180,
            int(72 * 1.3),
        )
        kp_count = CharplotKnowledgePoint.objects.filter(
            chapter__journey=journey
        ).count()
        stats_line = (
            f"答对 {stats['correct']}/{stats['answered']} 题 · "
            f"正确率 {stats['accuracy']}% · 掌握 {kp_count} 个知识点"
        )
        draw.text((90, 460), stats_line, font=font_stats, fill=white)
        draw.text((90, 530), "来 CharPlot 一起闯关学知识", font=font_label, fill=white)

        directory = os.path.join(settings.MEDIA_ROOT, _OG_DIR)
        os.makedirs(directory, exist_ok=True)
        filename = f"{report.slug}.png"
        img.save(os.path.join(directory, filename))
        return f"{settings.MEDIA_URL}{_OG_DIR}/{filename}"
    except Exception:
        logger.exception("CharPlot OG 图生成失败: journey=%s", journey.id)
        return ""


def create_review_report(journey):
    """旅程全部通关后生成复盘报告 (PRD E-1), 幂等: 已存在直接返回.

    由 _settle_level_clear 在事务内调用; OG 图文件 IO 异常已在 render 内
    吞掉, 不污染通关结算事务. 快照生成后不可变 (分享页只读防篡改, E-2).
    """
    existing = CharplotReviewReport.objects.filter(journey=journey).first()
    if existing:
        return existing
    stats = build_report_stats(journey)
    og_title, og_description = _report_og_texts(journey, stats)
    report = CharplotReviewReport.objects.create(
        journey=journey,
        user=journey.user,
        slug=generate_report_slug(),
        knowledge_summary=build_knowledge_summary(journey),
        stats=stats,
        og_title=og_title,
        og_description=og_description,
    )
    og_image = render_og_image(report, journey, stats)
    if og_image:
        report.og_image = og_image
        report.save(update_fields=["og_image", "updated_at"])
    return report
