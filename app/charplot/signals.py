"""CharPlot 信号: 登录时惰性结算连胜 (Issue 02 补充).

用 user_logged_in 而非在登录视图内联: 视图零改动, 任意登录入口统一触发;
admin 登录按 path 过滤, 不视为学习活动 (与登录事件显式落库的取舍一致,
避免后台管理操作污染用户活动判定).
"""

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .models import CharplotProfile
from .services import settle_streak_on_login


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """前台登录后执行连胜结算; admin 登录 / 无 profile 用户跳过."""
    if request and request.path.startswith("/admin/"):
        return
    try:
        profile = user.charplot_profile
    except CharplotProfile.DoesNotExist:
        return
    settle_streak_on_login(profile)
