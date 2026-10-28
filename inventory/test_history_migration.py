from datetime import datetime, timezone as dt_timezone

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ResolvedReportsBecomeStatusTests(TransactionTestCase):
    """Production has resolved reports from before status folded resolved in."""

    migrate_from = [("inventory", "0019_stockreport_status_notes")]
    migrate_to = [("inventory", "0020_stockreport_history")]

    def test_resolved_reports_gain_status_and_a_history_row(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Part = old_apps.get_model("inventory", "Part")
        StockReport = old_apps.get_model("inventory", "StockReport")
        User = old_apps.get_model("accounts", "User")

        part = Part.objects.create(part_number="1000", short_name="Jumper wires")
        user = User.objects.create(username="admin@andrew.cmu.edu")
        resolved_at = datetime(2026, 10, 27, 15, 0, tzinfo=dt_timezone.utc)
        report = StockReport.objects.create(
            part=part,
            kind="out",
            reported_by="x",
            status="order_placed",
            resolved_at=resolved_at,
            resolved_by=user,
        )
        untouched = StockReport.objects.create(part=part, kind="low", reported_by="y")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        StockReport = new_apps.get_model("inventory", "StockReport")
        Change = new_apps.get_model("inventory", "StockReportChange")

        report = StockReport.objects.get(pk=report.pk)
        self.assertEqual(report.status, "resolved")
        self.assertEqual(report.resolved_at, resolved_at)
        self.assertEqual(report.last_updated_by_id, user.pk)
        change = Change.objects.get(report_id=report.pk)
        self.assertEqual((change.old_status, change.new_status), ("order_placed", "resolved"))
        self.assertEqual(change.changed_at, resolved_at)

        untouched = StockReport.objects.get(pk=untouched.pk)
        self.assertEqual(untouched.status, "")
        self.assertFalse(Change.objects.filter(report_id=untouched.pk).exists())
