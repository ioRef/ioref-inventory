import csv
import io
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from inventory.export import label_rows
from inventory.models import Part


class LabelRowsTests(TestCase):
    def test_blank_label_text_falls_back_to_short_name(self):
        part = Part(part_number="0347", short_name="Keyboard switch", label_text="   ")
        self.assertEqual(label_rows([part], "ioref.org"), [["0347", "Keyboard switch", "ioref.org/0347"]])

    def test_label_text_is_used_and_stripped(self):
        part = Part(part_number="0750", short_name="Female header", label_text=" Header pins ")
        self.assertEqual(label_rows([part], "ioref.org"), [["0750", "Header pins", "ioref.org/0750"]])

    def test_url_has_no_protocol(self):
        part = Part(part_number="0001", short_name="x")
        self.assertEqual(label_rows([part], "ioref.org")[0][2], "ioref.org/0001")


@override_settings(LABEL_URL_HOST="ioref.org")
class LabelCsvEndpointTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(username="admin@andrew.cmu.edu", password="x")
        self.client.force_login(user)
        self.url = reverse("admin:inventory_part_grid_labels")
        self.switch = Part.objects.create(part_number="0347", short_name="Keyboard switch")
        self.header = Part.objects.create(part_number="0750", short_name="Female header", label_text="Header pins")
        self.other = Part.objects.create(part_number="0100", short_name="Unselected")

    def post(self, ids):
        return self.client.post(self.url, json.dumps({"ids": ids}), content_type="application/json")

    def test_selected_parts_download_as_label_csv_sorted_by_part_number(self):
        response = self.post([self.header.pk, self.switch.pk])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn('attachment; filename="ioref-labels-', response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(
            rows,
            [
                ["part_num", "label_text", "URL"],
                ["0347", "Keyboard switch", "ioref.org/0347"],
                ["0750", "Header pins", "ioref.org/0750"],
            ],
        )

    def test_no_selection_is_refused(self):
        response = self.post([])
        self.assertEqual(response.status_code, 400)

    def test_user_without_change_permission_is_refused(self):
        staff = get_user_model().objects.create_user(username="student@andrew.cmu.edu", password="x", is_staff=True)
        self.client.force_login(staff)
        response = self.post([self.switch.pk])
        self.assertEqual(response.status_code, 403)
