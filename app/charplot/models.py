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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = "charplot_profile"
        verbose_name = "CharPlot 用户扩展"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"charplot_profile({self.user_id})"
