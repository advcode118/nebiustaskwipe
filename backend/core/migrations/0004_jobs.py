import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_run_attempts"),
    ]

    operations = [
        migrations.CreateModel(
            name="CraftExample",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False, editable=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("caption", "caption"),
                            ("title", "title"),
                            ("description", "description"),
                            ("post", "post"),
                            ("rule", "rule"),
                            ("other", "other"),
                        ],
                        default="caption",
                        max_length=32,
                    ),
                ),
                ("text", models.TextField()),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddField(
            model_name="run",
            name="job",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="run",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "pending"),
                    ("running", "running"),
                    ("awaiting_job", "awaiting_job"),
                    ("awaiting_plan", "awaiting_plan"),
                    ("awaiting_value", "awaiting_value"),
                    ("awaiting_file", "awaiting_file"),
                    ("awaiting_confirm", "awaiting_confirm"),
                    ("completed", "completed"),
                    ("failed", "failed"),
                    ("paused", "paused"),
                    ("blocked", "blocked"),
                ],
                default="pending",
                max_length=32,
            ),
        ),
    ]
