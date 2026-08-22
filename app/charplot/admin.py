from django.contrib import admin

from .models import CharplotProfile, CharplotUserEvent


@admin.register(CharplotProfile)
class CharplotProfileAdmin(admin.ModelAdmin):
    """CharPlot 用户扩展后台管理."""

    list_display = (
        "user",
        "xp",
        "level",
        "streak",
        "max_streak",
        "hearts",
        "coins",
        "last_study_date",
        "freeze_until",
        "updated_at",
    )
    list_select_related = ("user",)
    search_fields = ("user__username",)
    readonly_fields = ("max_streak",)  # 由结算逻辑维护, 禁止手动修改


@admin.register(CharplotUserEvent)
class CharplotUserEventAdmin(admin.ModelAdmin):
    """CharPlot 用户事件事实表后台管理."""

    list_display = ("user", "event_type", "event_date", "created_at")
    list_filter = ("event_type",)
    list_select_related = ("user",)
    search_fields = ("user__username",)
