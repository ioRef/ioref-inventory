from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import ApiKey
from inventory.models import Part, StockReport


@override_settings(STOCK_REPORT_EMAIL=["owner@andrew.cmu.edu"])
class StockReportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.part = Part.objects.create(part_number="1000", short_name="Jumper wires")
        _, self.read_token = ApiKey.generate("ioref-web", scope=ApiKey.Scope.READ)
        self.url = "/api/v1/parts/1000/stock-reports/"

    def auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_read_only_key_can_file_a_report_and_it_is_emailed(self):
        self.auth(self.read_token)
        response = self.client.post(
            self.url,
            {"kind": "out", "reported_by": "rzachari@andrew.cmu.edu"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        report = StockReport.objects.get()
        self.assertEqual(report.reported_by, "rzachari@andrew.cmu.edu")
        self.assertEqual(report.kind, "out")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@andrew.cmu.edu"])
        self.assertIn("out of stock", mail.outbox[0].subject)

    def test_a_repeat_report_is_not_filed_or_emailed_again(self):
        self.auth(self.read_token)
        body = {"kind": "low", "reported_by": "x@andrew.cmu.edu"}
        self.assertEqual(
            self.client.post(self.url, body, format="json").status_code, 201
        )
        self.assertEqual(
            self.client.post(self.url, body, format="json").status_code, 200
        )
        self.assertEqual(StockReport.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

        # A different kind, or a report after resolution, is news.
        self.client.post(self.url, {"kind": "out", "reported_by": "y"}, format="json")
        StockReport.objects.filter(kind="low").update(
            resolved_at="2026-10-26T12:00:00Z"
        )
        self.client.post(self.url, body, format="json")
        self.assertEqual(StockReport.objects.count(), 3)
        self.assertEqual(len(mail.outbox), 3)

    def test_read_only_key_still_cannot_write_stock(self):
        self.auth(self.read_token)
        response = self.client.post(
            "/api/v1/parts/1000/stock-events/",
            {"kind": "inventory", "quantity": 3},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_unknown_kind_is_refused(self):
        self.auth(self.read_token)
        response = self.client.post(
            self.url, {"kind": "maybe", "reported_by": "x"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StockReport.objects.count(), 0)

    def test_anonymous_cannot_file(self):
        response = self.client.post(
            self.url, {"kind": "low", "reported_by": "x"}, format="json"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(StockReport.objects.count(), 0)

    @override_settings(STOCK_REPORT_EMAIL=[])
    def test_report_is_kept_when_no_recipient_is_configured(self):
        self.auth(self.read_token)
        self.client.post(self.url, {"kind": "low", "reported_by": "x"}, format="json")
        self.assertEqual(StockReport.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_open_reports_are_listed_and_resolved_ones_are_not(self):
        self.auth(self.read_token)
        self.client.post(self.url, {"kind": "low", "reported_by": "x"}, format="json")
        StockReport.objects.create(
            part=self.part, kind="out", reported_by="y", resolved_at=None
        )
        StockReport.objects.filter(kind="low").update(
            resolved_at="2026-10-26T12:00:00Z"
        )
        listed = self.client.get(self.url).json()
        self.assertEqual([r["kind"] for r in listed], ["out"])

    def test_admin_resolves_reports_without_deleting_them(self):
        from django.contrib.auth import get_user_model

        admin = get_user_model().objects.create_superuser(
            username="admin@andrew.cmu.edu", password="x"
        )
        report = StockReport.objects.create(part=self.part, kind="out", reported_by="y")
        self.client.force_login(admin)
        self.client.post(
            "/admin/inventory/stockreport/",
            {"action": "mark_resolved", "_selected_action": [report.pk]},
        )
        report.refresh_from_db()
        self.assertIsNotNone(report.resolved_at)
        self.assertEqual(report.status, "resolved")
        self.assertEqual(report.last_updated_by, admin)
        self.assertEqual(StockReport.objects.count(), 1)
