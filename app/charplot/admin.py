from django.contrib import admin

from .models import (
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
    CharplotProfile,
    CharplotUserEvent,
)


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


@admin.register(CharplotJourney)
class CharplotJourneyAdmin(admin.ModelAdmin):
    """学习旅程后台管理 (Issue 03, 便于人工验证图谱落库)."""

    list_display = (
        "id",
        "title",
        "user",
        "input_type",
        "status",
        "cleared",
        "created_at",
    )
    list_filter = ("status", "input_type", "cleared")
    list_select_related = ("user",)
    search_fields = ("title", "user__username")
    readonly_fields = ("graph",)


@admin.register(CharplotChapter)
class CharplotChapterAdmin(admin.ModelAdmin):
    """章节后台管理."""

    list_display = ("id", "title", "journey", "order")
    list_select_related = ("journey",)
    search_fields = ("title", "journey__title")


@admin.register(CharplotKnowledgePoint)
class CharplotKnowledgePointAdmin(admin.ModelAdmin):
    """知识点后台管理."""

    list_display = ("id", "title", "chapter", "order", "error_score")
    list_select_related = ("chapter__journey",)
    search_fields = ("title",)
