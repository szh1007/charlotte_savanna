from django.apps import AppConfig
from django.core.signals import request_started


class MinimallConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "minimall"
    verbose_name = "Shop"

    def ready(self):
        import minimall.signals  # noqa: F401

        # 首次请求时预热缓存, 避免 AppConfig.ready() 中访问 DB 的警告
        request_started.connect(_warmup_on_first_request, dispatch_uid="minimall_warmup")


_warmed = False


def _warmup_on_first_request(sender, environ, **kwargs):
    global _warmed
    if _warmed:
        return
    _warmed = True
    from .cache import warmup_cache

    warmup_cache()
