from django.apps import AppConfig


class CharplotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.charplot"
    # label 固定为 "charplot", 保证 db_table / django_migrations 记录 / 迁移依赖不变
    label = "charplot"
    verbose_name = "CharPlot"

    def ready(self):
        # 注册登录结算信号 (惰性连胜归零判定)
        from . import signals  # noqa: F401
