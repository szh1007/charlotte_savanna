from django.apps import AppConfig


class MinimallConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "minimall"

    def ready(self):
        pass
