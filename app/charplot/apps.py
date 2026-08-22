from django.apps import AppConfig


class CharplotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.charplot"
    # label 固定为 "charplot", 保证 db_table / django_migrations 记录 / 迁移依赖不变
    label = "charplot"
    verbose_name = "CharPlot"
