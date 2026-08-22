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
        "hearts",
        "coins",
        "last_study_date",
        "freeze_until",
        "updated_at",
    )
    list_select_related = ("user",)
    search_fields = ("user__username",)


@admin.register(CharplotUserEvent)
class CharplotUserEventAdmin(admin.ModelAdmin):
    """CharPlot 用户事件事实表后台管理."""

    list_display = ("user", "event_type", "event_date", "created_at")
    list_filter = ("event_type",)
    list_select_related = ("user",)
    search_fields = ("user__username",)
