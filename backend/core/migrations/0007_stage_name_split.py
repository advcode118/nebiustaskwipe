from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_vault_extract"),
    ]

    operations = [
        migrations.AddField(
            model_name="stage",
            name="name",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="stage",
            name="split_yours",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="stage",
            name="split_this_run",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
