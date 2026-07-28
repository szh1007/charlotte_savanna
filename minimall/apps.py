from django.apps import AppConfig


class MinimallConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "minimall"
    verbose_name = "Shop"

    def ready(self):
        import minimall.signals  # noqa: F401

        # 启动时预热缓存 (避免冷启动首次请求穿透)
        from .cache import warmup_cache

        warmup_cache()
