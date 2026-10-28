import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def fold_resolved_into_status(apps, schema_editor):
    """Resolved used to be a timestamp alone. Each resolved report becomes status Resolved,
    with a history row that says when and by whom, so the history matches what happened."""
    StockReport = apps.get_model("inventory", "StockReport")
    StockReportChange = apps.get_model("inventory", "StockReportChange")
    for report in StockReport.objects.filter(resolved_at__isnull=False).exclude(status="resolved"):
        StockReportChange.objects.create(
            report=report,
            changed_at=report.resolved_at or timezone.now(),
            changed_by_id=report.last_updated_by_id,
            old_status=report.status,
            new_status="resolved",
        )
        report.status = "resolved"
        report.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0019_stockreport_status_notes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(
            model_name="stockreport",
            old_name="resolved_by",
            new_name="last_updated_by",
        ),
        migrations.AlterField(
            model_name="stockreport",
            name="last_updated_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Whoever last changed the status or notes.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="stockreport",
            name="status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("order_placed", "Order placed"),
                    ("order_queued", "Order queued"),
                    ("order_received", "Order received, not yet processed"),
                    ("part_unavailable", "Part unavailable"),
                    ("not_restocked", "Part will not be restocked"),
                    ("other", "Other (see notes)"),
                    ("resolved", "Resolved: part is restocked"),
                ],
                default="",
                help_text="Where the lab is with this report. Blank until someone sets it.",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="StockReportChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("changed_at", models.DateTimeField(default=timezone.now)),
                ("old_status", models.CharField(blank=True, max_length=32)),
                ("new_status", models.CharField(blank=True, max_length=32)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="changes",
                        to="inventory.stockreport",
                    ),
                ),
            ],
            options={"ordering": ("changed_at", "id")},
        ),
        migrations.RunPython(fold_resolved_into_status, migrations.RunPython.noop),
    ]
