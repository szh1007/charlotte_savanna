"""CharPlot 数据模型.

命名约定: 模型统一带 charplot_ 前缀 (SPEC §2), 表名与模型名一致.
"""

import os

from django.conf import settings
from django.db import models
from django.utils import timezone


class CharplotProfile(models.Model):
    """用户扩展 - 与 auth_user 通过 OneToOne 关联.

    游戏化状态挂载于此 (SPEC §8): XP / 等级 / 连胜 / 心动值 / 学习币.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="charplot_profile",
        verbose_name="用户",
    )
    xp = models.PositiveIntegerField(default=0, verbose_name="经验值")
    level = models.PositiveIntegerField(default=1, verbose_name="等级")
    streak = models.PositiveIntegerField(default=0, verbose_name="当前连胜")
    max_streak = models.PositiveIntegerField(default=0, verbose_name="最大连胜")
    hearts = models.PositiveIntegerField(default=5, verbose_name="心动值")
    coins = models.PositiveIntegerField(default=0, verbose_name="学习币")
    last_study_date = models.DateField(
        null=True, blank=True, verbose_name="最后学习日期"
    )
    # Issue 05 答题/通关结算时更新; null = 从未学习
    freeze_until = models.DateField(
        null=True, blank=True, verbose_name="连胜冻结截止日期"
    )
    # 冻结保护到该日(含当日); null = 无冻结; 可叠加兑换顺延
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "charplot_profile"
        verbose_name = "CharPlot 用户扩展"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"charplot_profile({self.user_id})"


class CharplotUserEvent(models.Model):
    """用户事件事实表 (SPEC §8) - 统计源, 全记录按需聚合.

    登录天数 / 通关数等统计均由此表聚合; 答题逐题明细归 charplot_attempt
    (Issue 05), 此处只记录统计级事实.
    """

    class EventType(models.TextChoices):
        LOGIN = "login", "登录"
        LEVEL_CLEAR = "level_clear", "通关"  # Issue 05 使用
        ANSWER = "answer", "答题"  # Issue 05 使用

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="charplot_user_events",
        verbose_name="用户",
    )
    event_type = models.CharField(
        max_length=32, choices=EventType.choices, verbose_name="事件类型"
    )
    event_date = models.DateField(verbose_name="事件日期")
    # 按自然日统计(登录天数), 跨日学习/结算也按日
    payload = models.JSONField(default=dict, blank=True, verbose_name="附加数据")
    # 预留: 后续事件附带 level_id / score 等, 不破坏表结构
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "charplot_user_event"
        verbose_name = "CharPlot 用户事件"
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(
                fields=["user", "event_type", "event_date"],
                name="idx_user_event_type_date",
            )
        ]
        ordering = ["-event_date", "-created_at"]

    def __str__(self):
        return (
            f"charplot_user_event({self.user_id}, {self.event_type}, {self.event_date})"
        )


def journey_file_upload_to(instance, filename):
    """旅程源文件存储路径: app/charplot/uploads/ (MEDIA_ROOT=BASE_DIR).

    注意: FileField 首次 save 时 (pk 分配前) 计算 upload_to, instance.id 为
    None, 用 user_id + 时间戳区分同名文件 (minimall avatar_upload_to 同款).
    """
    ext = os.path.splitext(filename)[1]
    ts = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"app/charplot/uploads/user_{instance.user_id}_{ts}{ext}"


class CharplotJourney(models.Model):
    """学习旅程 (SPEC §8) - 一次输入产出的完整学习单元: 知识图谱 + 关卡集.

    graph 为管道原始产出快照 (含管道临时 id), 供 Issue 07 契约验证与审计;
    权威结构 = charplot_chapter / charplot_knowledge_point 规范化表 (API 返回).
    """

    class InputType(models.TextChoices):
        TEXT = "text", "纯文本"
        FILE = "file", "文件"
        LINK = "link", "网页链接"

    class Status(models.TextChoices):
        GENERATING = "generating", "生成中"
        READY = "ready", "已就绪"
        FAILED = "failed", "生成失败"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="charplot_journeys",
        verbose_name="用户",
    )
    title = models.CharField(max_length=200, verbose_name="标题")
    # 创建时由 content / 文件名推导 (derive_journey_title)
    input_type = models.CharField(
        max_length=16, choices=InputType.choices, verbose_name="输入类型"
    )
    content = models.TextField(blank=True, verbose_name="输入内容")
    # text/link 存原文; file 输入为空 (文件内容解析是 Issue 07)
    source_file = models.FileField(
        upload_to=journey_file_upload_to,
        blank=True,
        null=True,
        verbose_name="源文件",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.GENERATING,
        verbose_name="状态",
    )
    graph = models.JSONField(default=dict, blank=True, verbose_name="图谱快照")
    # 管道原始产出 (含临时 id), 快照/审计用, API 不返回
    latest_task_id = models.CharField(
        max_length=64, blank=True, verbose_name="最近任务 ID"
    )
    # 任务结束时经内部端点写入; 生成中由前端内存态持有 (route query)
    cleared = models.BooleanField(default=False, verbose_name="是否已通关")
    # 本票无关卡系统, 默认 False; Issue 05 通关结算时置 True
    error_message = models.TextField(blank=True, verbose_name="失败原因")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "charplot_journey"
        verbose_name = "CharPlot 学习旅程"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="idx_journey_user_created",
            )
        ]

    def __str__(self):
        return f"charplot_journey({self.id}, {self.title})"


class CharplotChapter(models.Model):
    """章节 (SPEC §8) - 知识图谱的第一层分组, 末尾可挂 Boss 战 (二期)."""

    journey = models.ForeignKey(
        CharplotJourney,
        on_delete=models.CASCADE,
        related_name="chapters",
        verbose_name="旅程",
    )
    title = models.CharField(max_length=200, verbose_name="标题")
    summary = models.TextField(blank=True, verbose_name="概述")
    order = models.PositiveIntegerField(default=0, verbose_name="序号")

    class Meta:
        db_table = "charplot_chapter"
        verbose_name = "CharPlot 章节"
        verbose_name_plural = verbose_name
        ordering = ["order", "id"]
        indexes = [
            models.Index(
                fields=["journey", "order"],
                name="idx_chapter_journey_order",
            )
        ]

    def __str__(self):
        return f"charplot_chapter({self.id}, {self.title})"


class CharplotKnowledgePoint(models.Model):
    """知识点 (SPEC §8) - 图谱原子节点.

    prerequisites 自引用 M2M 构成前置依赖有向图, 是技能树 / 关卡 / 间隔复习的
    锚点; error_score 为易错分 (答错 +2 / 答对 -1, 下限 0), Issue 05 结算更新.
    """

    chapter = models.ForeignKey(
        CharplotChapter,
        on_delete=models.CASCADE,
        related_name="knowledge_points",
        verbose_name="章节",
    )
    title = models.CharField(max_length=200, verbose_name="标题")
    summary = models.TextField(blank=True, verbose_name="概述")
    order = models.PositiveIntegerField(default=0, verbose_name="序号")
    prerequisites = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="dependents",
        verbose_name="前置依赖",
    )
    error_score = models.PositiveIntegerField(default=0, verbose_name="易错分")

    class Meta:
        db_table = "charplot_knowledge_point"
        verbose_name = "CharPlot 知识点"
        verbose_name_plural = verbose_name
        ordering = ["order", "id"]

    def __str__(self):
        return f"charplot_knowledge_point({self.id}, {self.title})"


class CharplotLevel(models.Model):
    """关卡 (SPEC §8) - 挂知识点, 5-8 题, 3 分钟量级.

    进度持久化字段: hearts 为本关剩余心动值 (5 心, 答错 -1, 扣完需重开),
    current_index 为下一题下标 (0-based), 通关 = cleared. 中途退出再进
    按这两个字段断点续答; 重开 = hearts/current_index 重置, 历史 Attempt 保留.
    """

    journey = models.ForeignKey(
        CharplotJourney,
        on_delete=models.CASCADE,
        related_name="levels",
        verbose_name="旅程",
    )
    knowledge_point = models.ForeignKey(
        CharplotKnowledgePoint,
        on_delete=models.CASCADE,
        related_name="levels",
        verbose_name="知识点",
    )
    hearts = models.PositiveIntegerField(default=5, verbose_name="剩余心动值")
    current_index = models.PositiveIntegerField(default=0, verbose_name="下一题下标")
    cleared = models.BooleanField(default=False, verbose_name="是否已通关")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "charplot_level"
        verbose_name = "CharPlot 关卡"
        verbose_name_plural = verbose_name
        # 知识点与关卡 1:N (大知识点拆多关, Issue 08 间隔复习), 允许重复
        indexes = [
            models.Index(
                fields=["journey", "knowledge_point"],
                name="idx_level_journey_kp",
            )
        ]
        ordering = ["knowledge_point__order", "id"]

    def __str__(self):
        return f"charplot_level({self.id}, kp={self.knowledge_point_id})"


class CharplotQuestion(models.Model):
    """题目 (SPEC §8) - 选择 / 判断 / 填空; 含预生成讲解与来源引用位.

    options 仅选择/判断类型使用 (判断 = 固定 [对, 错] 选项, 由前端内置);
    answer 存 JSON: 选择 = 正确选项下标 int, 判断 = "true"/"false", 填空 =
    可接受答案字符串数组 (归一化后模糊匹配). sources 为来源引用数组,
    Issue 08 真实管道填充, 本票 stub 留空占位.
    """

    class QuestionType(models.TextChoices):
        CHOICE = "choice", "选择"
        JUDGE = "judge", "判断"
        FILL = "fill", "填空"

    level = models.ForeignKey(
        CharplotLevel,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name="关卡",
    )
    question_type = models.CharField(
        max_length=16, choices=QuestionType.choices, verbose_name="题型"
    )
    content = models.TextField(verbose_name="题干")
    options = models.JSONField(default=list, blank=True, verbose_name="选项")
    answer = models.JSONField(default=list, blank=True, verbose_name="标准答案")
    # 选择=int / 判断=str / 填空=list[str], 判分按题型取用
    explanation = models.TextField(blank=True, verbose_name="讲解")
    sources = models.JSONField(default=list, blank=True, verbose_name="来源引用")
    order = models.PositiveIntegerField(default=0, verbose_name="序号")

    class Meta:
        db_table = "charplot_question"
        verbose_name = "CharPlot 题目"
        verbose_name_plural = verbose_name
        ordering = ["order", "id"]
        indexes = [
            models.Index(
                fields=["level", "order"],
                name="idx_question_level_order",
            )
        ]

    def __str__(self):
        return f"charplot_question({self.id}, {self.question_type})"


class CharplotReviewReport(models.Model):
    """复盘报告 (SPEC §8) - 通关总结, 公开链接页可分享 (Issue 06).

    旅程全部关卡通关时由服务层生成并落库快照: 知识总结 (章节 → 知识点)
    + 答题统计 (生成时点从 Attempt 聚合, 与事实表一致). 快照生成后不可变,
    分享页只读展示; slug 不可猜测 → 内容不可篡改 (PRD E-2).

    og_image 为 Pillow 绘制的社交卡片 PNG 相对 URL (media 下); 生成失败时
    为空串 (OG 卡片仅标题/摘要, 不阻塞报告).
    """

    journey = models.OneToOneField(
        CharplotJourney,
        on_delete=models.CASCADE,
        related_name="review_report",
        verbose_name="旅程",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="charplot_review_reports",
        verbose_name="用户",
    )
    slug = models.CharField(max_length=16, unique=True, verbose_name="公开短链")
    # 密码学随机短码, 不可猜测; unique 约束防撞库
    knowledge_summary = models.JSONField(default=dict, verbose_name="知识总结快照")
    stats = models.JSONField(default=dict, verbose_name="答题统计快照")
    og_title = models.CharField(max_length=120, blank=True, verbose_name="OG 标题")
    og_description = models.CharField(
        max_length=200, blank=True, verbose_name="OG 描述"
    )
    og_image = models.CharField(max_length=200, blank=True, verbose_name="OG 缩略图")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "charplot_review_report"
        verbose_name = "CharPlot 复盘报告"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"charplot_review_report({self.id}, {self.slug})"


class CharplotAttempt(models.Model):
    """答题记录 (SPEC §8) - 逐题事实, 统计与分析的事实源.

    关卡重开产生新记录, 历史 Attempt 保留不覆盖 (掌握度分析需要历史事实);
    不做唯一约束. user_answer 存用户原始作答 (填空原文 / 选项下标 / 判断值),
    duration 为作答耗时秒数 (前端计时的宽松参考, 后端不强制).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="charplot_attempts",
        verbose_name="用户",
    )
    level = models.ForeignKey(
        CharplotLevel,
        on_delete=models.CASCADE,
        related_name="attempts",
        verbose_name="关卡",
    )
    question = models.ForeignKey(
        CharplotQuestion,
        on_delete=models.CASCADE,
        related_name="attempts",
        verbose_name="题目",
    )
    is_correct = models.BooleanField(verbose_name="是否正确")
    user_answer = models.JSONField(default=list, blank=True, verbose_name="用户作答")
    duration = models.PositiveIntegerField(default=0, verbose_name="耗时(秒)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        db_table = "charplot_attempt"
        verbose_name = "CharPlot 答题记录"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="idx_attempt_user_created",
            ),
            models.Index(
                fields=["level", "question"],
                name="idx_attempt_level_question",
            ),
        ]

    def __str__(self):
        return (
            f"charplot_attempt({self.user_id}, q={self.question_id}, {self.is_correct})"
        )
