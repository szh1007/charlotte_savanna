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


def _warmup_on_first_request(sender, environ, **kwargs):
    global _warmed
    if _warmed:
        return
    _warmed = True
    from .cache import warmup_cache

    warmup_cache()
