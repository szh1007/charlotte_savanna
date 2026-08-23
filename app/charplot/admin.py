from django.contrib import admin

from .models import (
    CharplotAttempt,
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgeBase,
    CharplotKnowledgeBaseDocument,
    CharplotKnowledgePoint,
    CharplotLevel,
    CharplotProfile,
    CharplotQuestion,
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


@admin.register(CharplotLevel)
class CharplotLevelAdmin(admin.ModelAdmin):
    """关卡后台管理 (Issue 05): 进度 / 剩余心 / 通关状态, 便于人工验证."""

    list_display = (
        "id",
        "journey",
        "knowledge_point",
        "hearts",
        "current_index",
        "cleared",
        "updated_at",
    )
    list_filter = ("cleared",)
    list_select_related = ("journey", "knowledge_point")
    search_fields = ("knowledge_point__title",)
    readonly_fields = ("hearts", "current_index", "cleared")  # 由结算逻辑维护


@admin.register(CharplotQuestion)
class CharplotQuestionAdmin(admin.ModelAdmin):
    """题目后台管理."""

    list_display = ("id", "level", "question_type", "order", "content")
    list_filter = ("question_type",)
    list_select_related = ("level",)
    search_fields = ("content",)


@admin.register(CharplotAttempt)
class CharplotAttemptAdmin(admin.ModelAdmin):
    """答题记录后台管理 (Issue 05, 统计事实源)."""

    list_display = ("id", "user", "level", "question", "is_correct", "created_at")
    list_filter = ("is_correct",)
    list_select_related = ("user", "level", "question")
    search_fields = ("user__username",)
    readonly_fields = (
        "user",
        "level",
        "question",
        "is_correct",
        "user_answer",
        "duration",
    )


@admin.register(CharplotKnowledgeBase)
class CharplotKnowledgeBaseAdmin(admin.ModelAdmin):
    """知识库后台管理 (Issue 09, 便于人工验证状态机与 collection 配置)."""

    list_display = (
        "id",
        "name",
        "status",
        "collection_name",
        "latest_task_id",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("name",)
    readonly_fields = ("collection_name",)  # 创建时生成, 禁止手动修改


@admin.register(CharplotKnowledgeBaseDocument)
class CharplotKnowledgeBaseDocumentAdmin(admin.ModelAdmin):
    """知识库文档后台管理 (Issue 09): 软删标记便于人工验证恢复."""

    list_display = (
        "id",
        "knowledge_base",
        "title",
        "file_size",
        "is_deleted",
        "created_at",
    )
    list_filter = ("is_deleted",)
    list_select_related = ("knowledge_base",)
    search_fields = ("title", "knowledge_base__name")
