from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_run_current_url_run_nudged_run_outcome_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="run",
            name="fill_report",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="run",
            name="intent",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="run",
            name="kind",
            field=models.CharField(default="skill", max_length=20),
        ),
        migrations.AddField(
            model_name="run",
            name="needs_plan",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="run",
            name="plan_approved",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="run",
            name="plan_nudged",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="run",
            name="prompt",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="run",
            name="tab_title",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="run",
            name="skill",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="runs",
                to="core.skill",
            ),
        ),
        migrations.AlterField(
            model_name="run",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "pending"),
                    ("running", "running"),
                    ("awaiting_plan", "awaiting_plan"),
                    ("awaiting_value", "awaiting_value"),
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
