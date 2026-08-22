"""CharPlot 数据模型.

命名约定: 模型统一带 charplot_ 前缀 (SPEC §2), 表名与模型名一致.
"""

from django.conf import settings
from django.db import models


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
