from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_stage_name_split"),
    ]

    operations = [
        migrations.AddField(
            model_name="run",
            name="stage_index",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="run",
            name="checkpoint",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
