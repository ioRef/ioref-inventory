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
        self.change_url = reverse("admin:inventory_stockreport_change", args=[self.report.pk])

    def set_status(self, status, **extra):
        data = {"status": status, "notes": self.report.notes}
        data.update(extra)
        return self.client.post(self.change_url, data)

    def test_list_filters_and_sorts_on_every_column(self):
        for params in (
            {"status": "order_placed"},
            {"status": "resolved"},
            {"kind": "out"},
            {"reported_by": "rzachari"},
            {"notes__icontains": "waiting"},
            {"last_updated_by": self.user.pk},
            {"show_resolved": "1"},
            {"o": "3"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.list_url, params).status_code, 200)

    def test_resolved_reports_are_hidden_until_the_box_is_ticked(self):
        self.report.status = "resolved"
        self.report.save()
        self.assertNotContains(self.client.get(self.list_url), "rzachari@andrew.cmu.edu")
        response = self.client.get(self.list_url, {"show_resolved": "1"})
        self.assertContains(response, "rzachari@andrew.cmu.edu")

    def test_choosing_resolved_in_status_filter_shows_them(self):
        self.report.status = "resolved"
        self.report.save()
        response = self.client.get(self.list_url, {"status": "resolved"})
        self.assertContains(response, "rzachari@andrew.cmu.edu")

    def test_history_reads_as_original_report_then_changes(self):
        self.set_status("order_placed")
        lines = self.report.history_lines()
        self.assertEqual(
            lines[0],
            "Oct. 28, 2026, 10:05 a.m.: Original report by rzachari@andrew.cmu.edu: Out of stock",
        )
        self.assertTrue(lines[1].endswith(": updated to Order placed by Robert Zacharias"))
        self.assertEqual(len(lines), 2)

    def test_each_status_change_is_appended_to_history(self):
        self.set_status("order_placed")
        self.set_status("order_received")
        self.assertEqual(self.report.changes.count(), 2)
        self.assertEqual(self.report.changes.last().new_status, "order_received")
        self.assertEqual(len(self.report.history_lines()), 3)

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

    def test_mark_resolved_action_resolves_and_records_history(self):
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

    def test_long_notes_are_truncated_in_the_list(self):
        self.report.notes = "word " * 40
        self.report.save()
        self.assertContains(self.client.get(self.list_url), "...")
