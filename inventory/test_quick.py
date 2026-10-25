from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from inventory.models import Part, PartChange, StockEvent
from inventory.quick import QuickError, record_counts, undo_counts
from inventory.bulk import undoable_batches


class QuickInventoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="admin@andrew.cmu.edu", password="x")
        self.client.force_login(self.user)
        self.part = Part.objects.create(part_number="1930", short_name="DC motor", unit="each")

    def post(self, url, body):
        return self.client.post(url, data=body, content_type="application/json")

    def test_record_creates_one_event_per_count_entered(self):
        result = record_counts("1930", "10", "25", self.user)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(StockEvent.objects.filter(part=self.part, recorded_by=self.user).count(), 2)

    def test_a_blank_entry_is_refused(self):
        with self.assertRaises(QuickError):
            record_counts("1930", "", "", self.user)

    def test_a_non_number_is_refused(self):
        with self.assertRaises(QuickError):
            record_counts("1930", "ten", "", self.user)

    def test_lookup_reports_latest_counts_and_unit_text(self):
        self.part.unit = "feet"
        self.part.save()
        record_counts("1930", "500", "", self.user)
        data = self.client.get("/admin/inventory/part/quick-inventory/lookup/", {"part_number": "1930"}).json()
        self.assertEqual(data["inventory"]["text"], "500 feet")
        self.assertIsNone(data["backstock"])

    def test_unknown_part_is_reported(self):
        response = self.client.get("/admin/inventory/part/quick-inventory/lookup/", {"part_number": "9999"})
        self.assertEqual(response.status_code, 404)

    def test_undo_voids_the_count_and_the_previous_count_stands_again(self):
        first = record_counts("1930", "10", "", self.user)["events"][0]["id"]
        second = record_counts("1930", "12", "", self.user)["events"][0]["id"]
        undo_counts([second], self.user)
        self.assertEqual(StockEvent.objects.get(pk=second).quantity, 12)
        self.assertIsNotNone(StockEvent.objects.get(pk=second).voided_at)
        data = self.client.get("/admin/inventory/part/quick-inventory/lookup/", {"part_number": "1930"}).json()
        self.assertEqual(data["inventory"]["id"], first)

    def test_undo_removes_the_count_entirely_when_nothing_earlier_exists(self):
        event = record_counts("1930", "10", "", self.user)["events"][0]["id"]
        undo_counts([event], self.user)
        data = self.client.get("/admin/inventory/part/quick-inventory/lookup/", {"part_number": "1930"}).json()
        self.assertIsNone(data["inventory"])

    def test_a_newer_count_blocks_undo(self):
        older = record_counts("1930", "10", "", self.user)["events"][0]["id"]
        record_counts("1930", "12", "", self.user)
        with self.assertRaises(QuickError):
            undo_counts([older], self.user)

    def test_only_the_recorder_can_undo(self):
        other = get_user_model().objects.create_superuser(username="other@andrew.cmu.edu", password="x")
        event = record_counts("1930", "10", "", self.user)["events"][0]["id"]
        with self.assertRaises(QuickError):
            undo_counts([event], other)

    def test_stock_undo_is_not_on_the_bulk_edit_chain(self):
        event = record_counts("1930", "10", "", self.user)["events"][0]["id"]
        undo_counts([event], self.user)
        self.assertEqual(PartChange.objects.count(), 0)
        self.assertEqual(undoable_batches(), [])

    def test_voided_counts_are_left_out_of_the_api_and_public_counts(self):
        event = record_counts("1930", "10", "", self.user)["events"][0]["id"]
        undo_counts([event], self.user)
        response = self.client.get("/api/v1/parts/1930/")
        self.assertIsNone(response.json()["on_floor"])
