# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("minimall", "0013_add_image_version_and_timestamp_naming"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="sort_order",
            field=models.PositiveIntegerField(default=0, verbose_name="排序(越小越靠前)"),
        ),
    ]
