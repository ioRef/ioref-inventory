from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from inventory.models import Part, StockReport, StockReportChange
from inventory.reports import notify


@override_settings(
    STOCK_REPORT_EMAIL=["lab@andrew.cmu.edu", "help@ideate.cmu.edu"],
    DEFAULT_FROM_EMAIL="stock-report@ioref.org",
)
class ReportEmailTests(TestCase):
    def setUp(self):
        self.part = Part.objects.create(part_number="1000", short_name="Jumper wires")

    def test_one_message_goes_to_every_recipient(self):
        report = StockReport.objects.create(part=self.part, kind="out", reported_by="x@andrew.cmu.edu")
        notify(report)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["lab@andrew.cmu.edu", "help@ideate.cmu.edu"])

    def test_report_time_is_in_eastern_time_not_utc(self):
        # 12:00 UTC on 26 October 2026 is 08:00 in Eastern Daylight Time.
        report = StockReport.objects.create(
            part=self.part,
            kind="low",
            reported_by="x@andrew.cmu.edu",
            reported_at=datetime(2026, 10, 26, 12, 0, tzinfo=dt_timezone.utc),
        )
        notify(report)
        self.assertIn("Reported at 2026-10-26 08:00 EDT.", mail.outbox[0].body)


class StockReportAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin@andrew.cmu.edu", password="x", first_name="Robert", last_name="Zacharias"
        )
        self.client.force_login(self.user)
        self.part = Part.objects.create(part_number="1000", short_name="Jumper wires")
        self.report = StockReport.objects.create(
            part=self.part,
            kind="out",
            reported_by="rzachari@andrew.cmu.edu",
            reported_at=datetime(2026, 10, 28, 14, 5, tzinfo=dt_timezone.utc),
        )
        self.list_url = reverse("admin:inventory_stockreport_changelist")
        self.data_url = reverse("admin:inventory_stockreport_data")
        self.change_url = reverse("admin:inventory_stockreport_change", args=[self.report.pk])

    def set_status(self, status, **extra):
        data = {"status": status, "notes": self.report.notes}
        data.update(extra)
        return self.client.post(self.change_url, data)

    def rows(self):
        return self.client.get(self.data_url).json()["rows"]

    def test_list_is_a_grid_with_its_data_url(self):
        response = self.client.get(self.list_url)
        self.assertContains(response, 'id="sheet"')
        self.assertContains(response, f'"{self.data_url}"')
        self.assertContains(response, 'id="show-resolved"')

    def test_data_lists_every_report_and_marks_resolved_ones(self):
        self.set_status("resolved")
        self.report.refresh_from_db()
        StockReport.objects.create(part=self.part, kind="low", reported_by="y")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        resolved = [r for r in rows if r["is_resolved"]]
        self.assertEqual([r["id"] for r in resolved], [self.report.pk])

    def test_data_rows_carry_the_columns_the_grid_shows(self):
        row = self.rows()[0]
        self.assertEqual(row["part_number"], "1000")
        self.assertEqual(row["kind"], "Out of stock")
        self.assertEqual(row["status"], "No status")
        self.assertEqual(row["reported_at"], "Oct. 28, 2026, 10:05 a.m.")
        self.assertTrue(row["reported_at_sort"].startswith("2026-10-28T14:05"))
        self.assertEqual(row["resolved_at"], "")
        self.assertEqual(row["notes"], "")

    def test_history_text_reads_as_original_report_then_changes(self):
        self.set_status("order_placed")
        history = self.rows()[0]["history"].split("\n")
        self.assertEqual(
            history[0],
            "Oct. 28, 2026, 10:05 a.m.: Original report by rzachari@andrew.cmu.edu: Out of stock",
        )
        self.assertTrue(history[1].endswith(": updated to Order placed by Robert Zacharias"))

    def test_each_status_change_is_appended_to_history(self):
        self.set_status("order_placed")
        self.set_status("order_received")
        self.assertEqual(self.report.changes.count(), 2)
        self.assertEqual(self.report.changes.last().new_status, "order_received")
        self.assertEqual(len(self.report.history_lines()), 3)

    def test_status_gains_order_received_and_resolved(self):
        self.assertIn("order_received", [c for c, _ in StockReport.Status.choices])
        self.assertIn("resolved", [c for c, _ in StockReport.Status.choices])

    def test_resolving_sets_resolved_at_and_last_updated_by(self):
        self.set_status("resolved")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "resolved")
        self.assertIsNotNone(self.report.resolved_at)
        self.assertEqual(self.report.last_updated_by, self.user)

    def test_moving_away_from_resolved_clears_resolved_at(self):
        self.set_status("resolved")
        self.set_status("order_queued")
        self.report.refresh_from_db()
        self.assertIsNone(self.report.resolved_at)

    def test_notes_edits_update_last_updated_by_but_not_history(self):
        self.client.post(self.change_url, {"status": "", "notes": "Called the supplier."})
        self.report.refresh_from_db()
        self.assertEqual(self.report.notes, "Called the supplier.")
        self.assertEqual(self.report.last_updated_by, self.user)
        self.assertEqual(StockReportChange.objects.filter(report=self.report).count(), 0)

    def test_bulk_resolve_from_the_grid_resolves_and_records_history(self):
        self.client.post(
            self.list_url,
            {"action": "mark_resolved", "_selected_action": [self.report.pk]},
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "resolved")
        self.assertEqual(self.report.last_updated_by, self.user)
        self.assertEqual(StockReportChange.objects.filter(report=self.report, new_status="resolved").count(), 1)

    def test_status_and_notes_are_editable_and_everything_else_is_read_only(self):
        response = self.client.get(self.change_url)
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="notes"')
        self.assertNotContains(response, 'name="reported_by"')

    def test_resolved_report_still_opens_from_its_change_page(self):
        self.set_status("resolved")
        self.assertEqual(self.client.get(self.change_url).status_code, 200)

    def test_grid_is_only_for_people_who_can_see_reports(self):
        self.client.logout()
        response = self.client.get(self.data_url)
        self.assertNotEqual(response.status_code, 200)
