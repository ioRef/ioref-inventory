from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from inventory.models import Part, StockReport
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
            username="admin@andrew.cmu.edu", password="x"
        )
        self.client.force_login(self.user)
        self.part = Part.objects.create(part_number="1000", short_name="Jumper wires")
        self.report = StockReport.objects.create(
            part=self.part, kind="out", reported_by="x@andrew.cmu.edu"
        )
        self.list_url = reverse("admin:inventory_stockreport_changelist")
        self.change_url = reverse("admin:inventory_stockreport_change", args=[self.report.pk])

    def test_list_filters_and_sorts_on_every_column(self):
        for params in (
            {"status": "order_placed"},
            {"kind": "out"},
            {"reported_by": "x@andrew"},
            {"notes__icontains": "waiting"},
            {"resolved_by": self.user.pk},
            {"o": "3"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.list_url, params).status_code, 200)

    def test_resolved_by_column_names_the_user_who_resolved_it(self):
        self.client.post(self.list_url, {"action": "mark_resolved", "_selected_action": [self.report.pk]})
        self.report.refresh_from_db()
        self.assertEqual(self.report.resolved_by, self.user)
        self.assertContains(self.client.get(self.list_url), self.user.get_username())

    def test_status_and_notes_are_editable_and_everything_else_is_read_only(self):
        response = self.client.get(self.change_url)
        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="notes"')
        self.assertNotContains(response, 'name="reported_by"')

    def test_anyone_with_access_can_set_status_and_notes(self):
        self.client.post(
            self.change_url,
            {"status": "order_queued", "notes": "Ordered from Adafruit, waiting on stock."},
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "order_queued")
        self.assertEqual(self.report.notes, "Ordered from Adafruit, waiting on stock.")

    def test_long_notes_are_truncated_in_the_list(self):
        self.report.notes = "word " * 40
        self.report.save()
        response = self.client.get(self.list_url)
        self.assertContains(response, "...")
