from django.contrib.auth import get_user_model
from django.test import TestCase

from inventory.bulk import UNDO_DEPTH, BulkEditError, apply, plan, undo_last, undoable_batches
from inventory.models import Group, Location, Part, PartChange


class BulkEditTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="staff@andrew.cmu.edu")
        self.resistors = Group.objects.create(name="Resistors", slug="resistors")
        self.bench = Location.objects.create(code="B1-R1-C1", name="Resistors bin")
        self.a = Part.objects.create(part_number="0001", short_name="10Ω resistor", group=self.resistors)
        self.b = Part.objects.create(part_number="0002", short_name="22Ω resistor", group=self.resistors)
        self.ids = [self.a.pk, self.b.pk]

    def test_sets_a_text_field_on_every_part_and_logs_each(self):
        batch, count = apply(self.ids, "description", "resistor, carbon film", self.user)

        self.assertEqual(count, 2)
        self.a.refresh_from_db()
        self.assertEqual(self.a.description, "resistor, carbon film")
        logs = PartChange.objects.filter(batch=batch).order_by("part__part_number")
        self.assertEqual([c.part.part_number for c in logs], ["0001", "0002"])
        self.assertEqual(logs[0].old_value, "")
        self.assertEqual(logs[0].new_value, "resistor, carbon film")
        self.assertEqual(logs[0].changed_by, self.user)

    def test_part_number_is_refused(self):
        with self.assertRaises(BulkEditError):
            apply(self.ids, "part_number", "9999", self.user)
        self.assertEqual(PartChange.objects.count(), 0)

    def test_unknown_field_is_refused(self):
        with self.assertRaises(BulkEditError):
            apply(self.ids, "on_floor", "5", self.user)

    def test_group_and_category_are_not_bulk_editable(self):
        with self.assertRaises(BulkEditError):
            apply(self.ids, "group", "cables", self.user)
        with self.assertRaises(BulkEditError):
            apply(self.ids, "category", "input", self.user)

    def test_location_is_set_by_code(self):
        apply(self.ids, "location", "B1-R1-C1", self.user)
        self.a.refresh_from_db()
        self.assertEqual(self.a.location, self.bench)
        self.assertEqual(PartChange.objects.filter(field="location", new_value="B1-R1-C1").count(), 2)

    def test_unknown_location_code_is_refused(self):
        with self.assertRaises(BulkEditError):
            apply(self.ids, "location", "no-such-bin", self.user)

    def test_one_failing_part_blocks_the_whole_edit(self):
        self.b.min_quantity = 10
        self.b.max_quantity = 20
        self.b.save()
        with self.assertRaises(BulkEditError) as ctx:
            apply(self.ids, "max_quantity", "5", self.user)
        self.assertIn("0002", ctx.exception.errors)
        self.a.refresh_from_db()
        self.assertIsNone(self.a.max_quantity)
        self.assertEqual(PartChange.objects.count(), 0)

    def test_quantities_are_whole_numbers_and_blank_clears_them(self):
        apply(self.ids, "min_quantity", "3", self.user)
        self.a.refresh_from_db()
        self.assertEqual(self.a.min_quantity, 3)

        apply(self.ids, "min_quantity", "", self.user)
        self.a.refresh_from_db()
        self.assertIsNone(self.a.min_quantity)

        with self.assertRaises(BulkEditError):
            apply(self.ids, "min_quantity", "three", self.user)

    def test_required_fields_cannot_be_blanked(self):
        with self.assertRaises(BulkEditError):
            apply(self.ids, "short_name", "  ", self.user)
        apply(self.ids, "description", "", self.user)

    def test_expected_count_must_match(self):
        with self.assertRaises(BulkEditError):
            apply(self.ids, "unit", "meter", self.user, expect=1)
        self.a.refresh_from_db()
        self.assertEqual(self.a.unit, "each")

    def test_plan_writes_nothing(self):
        result = plan(self.ids, "unit", "feet")
        self.assertEqual(len(result.rows), 2)
        self.a.refresh_from_db()
        self.assertEqual(self.a.unit, "each")
        self.assertEqual(PartChange.objects.count(), 0)

    def test_a_missing_selected_part_is_refused(self):
        with self.assertRaises(BulkEditError):
            apply([self.a.pk, 999999], "unit", "feet", self.user)


class GridTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin@andrew.cmu.edu", password="x"
        )
        self.client.force_login(self.user)
        self.a = Part.objects.create(part_number="0001", short_name="10Ω resistor", unit="each")
        self.b = Part.objects.create(part_number="0002", short_name="22Ω resistor", unit="each")

    def test_page_renders_for_staff(self):
        response = self.client.get("/admin/inventory/part/grid/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="bulk-field"')

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get("/admin/inventory/part/grid/data/")
        self.assertEqual(response.status_code, 302)

    def test_data_lists_every_part_with_editable_columns_and_no_vendor(self):
        response = self.client.get("/admin/inventory/part/grid/data/")
        data = response.json()
        self.assertEqual([r["part_number"] for r in data["rows"]], ["0001", "0002"])
        self.assertNotIn("vendor", data["editable"])
        self.assertNotIn("group", data["editable"])
        self.assertNotIn("part_number", data["editable"])

    def test_preview_reports_changes_and_writes_nothing(self):
        response = self.client.post(
            "/admin/inventory/part/grid/preview/",
            data={"ids": [self.a.pk, self.b.pk], "field": "unit", "value": "feet"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)
        self.a.refresh_from_db()
        self.assertEqual(self.a.unit, "each")
        self.assertEqual(PartChange.objects.count(), 0)

    def test_apply_writes_and_returns_a_batch(self):
        response = self.client.post(
            "/admin/inventory/part/grid/apply/",
            data={"ids": [self.a.pk], "field": "unit", "value": "feet", "expect": 1},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.unit, "feet")
        self.assertEqual(PartChange.objects.filter(batch=response.json()["batch"]).count(), 1)

    def test_refused_edit_returns_errors_and_writes_nothing(self):
        response = self.client.post(
            "/admin/inventory/part/grid/apply/",
            data={"ids": [self.a.pk], "field": "part_number", "value": "9999"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(PartChange.objects.count(), 0)

    def test_malformed_body_is_rejected(self):
        response = self.client.post(
            "/admin/inventory/part/grid/preview/", data="not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)


class GridChoicesTests(TestCase):
    def test_status_and_location_choices_are_offered(self):
        user = get_user_model().objects.create_superuser(username="admin@andrew.cmu.edu", password="x")
        self.client.force_login(user)
        Location.objects.create(code="B1-R1-C1")
        choices = self.client.get("/admin/inventory/part/grid/data/").json()["choices"]
        self.assertEqual(set(choices["status"]), {"active", "unavailable", "to_discontinue", "discontinued"})
        self.assertIn("B1-R1-C1", choices["location"])


class UndoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="staff@andrew.cmu.edu")
        self.a = Part.objects.create(part_number="0001", short_name="10Ω resistor", description="old a")
        self.b = Part.objects.create(part_number="0002", short_name="22Ω resistor", description="")
        self.ids = [self.a.pk, self.b.pk]

    def test_undo_restores_each_part_to_its_own_old_value(self):
        apply(self.ids, "description", "new", self.user)
        undo_last(self.user)
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual(self.a.description, "old a")
        self.assertEqual(self.b.description, "")

    def test_undo_steps_back_one_batch_at_a_time(self):
        apply(self.ids, "unit", "feet", self.user)
        apply(self.ids, "unit", "meter", self.user)
        undo_last(self.user)
        self.a.refresh_from_db()
        self.assertEqual(self.a.unit, "feet")
        undo_last(self.user)
        self.a.refresh_from_db()
        self.assertEqual(self.a.unit, "each")
        with self.assertRaises(BulkEditError):
            undo_last(self.user)

    def test_undo_batches_are_not_themselves_offered(self):
        apply(self.ids, "unit", "feet", self.user)
        undo_last(self.user)
        self.assertEqual(undoable_batches(), [])

    def test_undo_is_recorded_as_its_own_batch(self):
        original, _ = apply(self.ids, "unit", "feet", self.user)
        undo_batch, count = undo_last(self.user)
        self.assertEqual(count, 2)
        self.assertEqual(PartChange.objects.filter(batch=undo_batch, reverts=original).count(), 2)

    def test_depth_is_limited(self):
        for unit in ["feet", "meter", "yard", "inch", "pound", "foot"]:
            apply(self.ids, "unit", unit, self.user)
        self.assertEqual(len(undoable_batches()), UNDO_DEPTH)

    def test_undo_endpoints(self):
        admin = get_user_model().objects.create_superuser(username="admin@andrew.cmu.edu", password="x")
        apply(self.ids, "unit", "feet", admin)
        self.client.force_login(admin)
        status = self.client.get("/admin/inventory/part/grid/undo/").json()
        self.assertEqual(status["depth"], 1)
        self.assertEqual(status["next"]["field"], "unit")
        response = self.client.post("/admin/inventory/part/grid/undo/", data={}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/admin/inventory/part/grid/undo/").json()["depth"], 0)


class ExistingInconsistencyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="staff@andrew.cmu.edu")
        self.part = Part.objects.create(part_number="0002", short_name="heat sink", min_quantity=2, max_quantity=0)

    def test_an_unrelated_edit_is_not_blocked_by_an_existing_min_over_max(self):
        apply([self.part.pk], "description", "clip type", self.user)
        self.part.refresh_from_db()
        self.assertEqual(self.part.description, "clip type")

    def test_editing_a_quantity_into_a_conflict_is_refused(self):
        self.part.max_quantity = 3
        self.part.save()
        with self.assertRaises(BulkEditError):
            apply([self.part.pk], "min_quantity", "9", self.user)


class ZeroMaximumTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="staff@andrew.cmu.edu")
        self.part = Part.objects.create(part_number="0010", short_name="flux pen", min_quantity=5)

    def test_a_zero_maximum_is_unset_for_the_minimum_check(self):
        self.part.max_quantity = 0
        self.part.save()
        apply([self.part.pk], "min_quantity", "7", self.user)
        self.part.refresh_from_db()
        self.assertEqual(self.part.min_quantity, 7)


class CellEditTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(username="admin@andrew.cmu.edu", password="x")
        self.client.force_login(self.admin)
        self.part = Part.objects.create(part_number="0001", short_name="10Ω resistor", unit="each")

    def test_a_cell_save_is_one_batch_and_appears_in_history_and_undo(self):
        response = self.client.post(
            "/admin/inventory/part/grid/cell/",
            data={"id": self.part.pk, "field": "unit", "value": "feet"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.part.refresh_from_db()
        self.assertEqual(self.part.unit, "feet")

        history = self.client.get("/admin/inventory/part/grid/history/").json()["batches"]
        self.assertEqual(history[0]["changes"], [{"part": "0001", "name": "10Ω resistor", "old": "each", "new": "feet"}])
        self.assertEqual(history[0]["by"], "admin@andrew.cmu.edu")

        undo = self.client.post("/admin/inventory/part/grid/undo/", data={}, content_type="application/json")
        self.assertEqual(undo.status_code, 200)
        self.part.refresh_from_db()
        self.assertEqual(self.part.unit, "each")

    def test_a_refused_cell_save_changes_nothing(self):
        response = self.client.post(
            "/admin/inventory/part/grid/cell/",
            data={"id": self.part.pk, "field": "short_name", "value": " "},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.part.refresh_from_db()
        self.assertEqual(self.part.short_name, "10Ω resistor")

    def test_history_marks_undone_batches(self):
        apply([self.part.pk], "unit", "feet", self.admin)
        undo_last(self.admin)
        history = self.client.get("/admin/inventory/part/grid/history/").json()["batches"]
        self.assertTrue(any(b["undone"] for b in history))
        self.assertTrue(any(b["is_undo"] for b in history))
