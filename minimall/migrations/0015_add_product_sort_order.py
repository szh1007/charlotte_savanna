# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("minimall", "0014_add_category_sort_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="sort_order",
            field=models.PositiveIntegerField(default=0, verbose_name="排序(越小越靠前)"),
        ),
        migrations.AlterModelOptions(
            name="product",
            options={
                "ordering": ["sort_order", "-created_at"],
                "verbose_name": "商品",
                "verbose_name_plural": "商品",
            },
        ),
    ]
