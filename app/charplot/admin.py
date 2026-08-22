from django.contrib import admin

from .models import CharplotProfile


@admin.register(CharplotProfile)
class CharplotProfileAdmin(admin.ModelAdmin):
    """CharPlot 用户扩展后台管理."""

    list_display = ("user", "xp", "level", "streak", "hearts", "coins", "updated_at")
    list_select_related = ("user",)
    search_fields = ("user__username",)
