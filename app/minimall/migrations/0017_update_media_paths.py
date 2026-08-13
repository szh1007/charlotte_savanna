# Generated manually on 2026-08-13 — fix media paths after moving app into /app
# minimall app 迁入 app/minimall 后, 物理 uploads 目录随目录移动,
# 但 DB 存量路径前缀仍为 "minimall/uploads/", 需同步为 "app/minimall/uploads/".

from django.db import migrations
from django.db.models import F, Value
from django.db.models.functions import Replace


def update_media_paths(apps, schema_editor):
    ProductImage = apps.get_model("minimall", "ProductImage")
    Profile = apps.get_model("minimall", "Profile")

    ProductImage.objects.filter(image__startswith="minimall/uploads/").update(
        image=Replace(
            F("image"), Value("minimall/uploads/"), Value("app/minimall/uploads/")
        )
    )
    Profile.objects.filter(avatar__startswith="minimall/uploads/").update(
        avatar=Replace(
            F("avatar"), Value("minimall/uploads/"), Value("app/minimall/uploads/")
        )
    )


def revert_media_paths(apps, schema_editor):
    ProductImage = apps.get_model("minimall", "ProductImage")
    Profile = apps.get_model("minimall", "Profile")

    ProductImage.objects.filter(image__startswith="app/minimall/uploads/").update(
        image=Replace(
            F("image"), Value("app/minimall/uploads/"), Value("minimall/uploads/")
        )
    )
    Profile.objects.filter(avatar__startswith="app/minimall/uploads/").update(
        avatar=Replace(
            F("avatar"), Value("app/minimall/uploads/"), Value("minimall/uploads/")
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("minimall", "0016_alter_category_options_alter_profile_options"),
    ]

    operations = [
        migrations.RunPython(update_media_paths, revert_media_paths),
    ]
