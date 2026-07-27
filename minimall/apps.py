from django.apps import AppConfig


class MinimallConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "minimall"
    verbose_name = "Shop"

    def ready(self):
        import minimall.signals  # noqa: F401
