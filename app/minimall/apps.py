from django.apps import AppConfig
from django.core.signals import request_started


class MinimallConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.minimall"
    # label 固定为 "minimall", 保证 db_table / django_migrations 记录 / 迁移依赖不变
    label = "minimall"
    verbose_name = "Shop"

    def ready(self):
        from . import signals  # noqa: F401

        # 首次请求时预热缓存, 避免 AppConfig.ready() 中访问 DB 的警告
        request_started.connect(
            _warmup_on_first_request, dispatch_uid="minimall_warmup"
        )


_warmed = False


def _warmup_on_first_request(sender, **kwargs):
    """首次请求到达时预热一次缓存 —— **两种处理器的递法都要吃**.

    这里曾经写死过一个位置参数 `environ` (2026-09-26 修): WSGI 那侧 Django 递的正是
    `environ` (对得上), 而 **ASGI 那侧递的是 `scope`** —— 于是真按 ASGI 部署时
    **第一个请求必 500** (`TypeError: ... missing 1 required positional argument`),
    而且只有第一个请求会撞上, 之后 `_warmed` 已经是 True, 症状自己消失 (最难查的
    那种). 它是被 issue 35 的真机脚本踩出来的 —— 那个脚本走 ASGI 直连.

    修法不是"两个字段都收下", 而是**都不收**: 这个函数关心的是「来了一次请求」,
    不是「那次请求长什么样」, 所以除 `sender` 外一概 `**kwargs` 收下不看. Django 的
    信号就是这么用的 (递什么由处理器决定, 接收方只取自己要的那几个), 写死字段等于
    把部署方式焊进这一行.
    """
    global _warmed
    if _warmed:
        return
    _warmed = True
    from .cache import warmup_cache

    warmup_cache()
