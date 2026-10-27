from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import ApiKey
from inventory.models import Part


class PartSearchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        _, token = ApiKey.generate("ioref-web", scope=ApiKey.Scope.READ)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        Part.objects.create(part_number="0347", short_name="keyboard switch")
        Part.objects.create(part_number="0100", short_name="resistor", description="for a keyboard matrix")
        Part.objects.create(part_number="0200", short_name="motor", supplier_part_num="KEYB-9")
        Part.objects.create(part_number="0300", short_name="sensor")

    def numbers(self, query):
        data = self.client.get("/api/v1/parts/", {"search": query}).json()
        return sorted(r["part_number"] for r in data["results"])

    def test_matches_name_description_and_supplier_number(self):
        self.assertEqual(self.numbers("keyboard"), ["0100", "0347"])
        self.assertEqual(self.numbers("keyb-9"), ["0200"])

    def test_a_part_matching_several_fields_is_listed_once(self):
        Part.objects.filter(part_number="0347").update(description="keyboard keyboard")
        self.assertEqual(self.numbers("keyboard").count("0347"), 1)

    def test_discontinued_parts_are_excluded_on_request(self):
        Part.objects.filter(part_number="0347").update(status=Part.Status.DISCONTINUED)
        data = self.client.get(
            "/api/v1/parts/", {"search": "keyboard", "include_discontinued": "0"}
        ).json()
        self.assertEqual([r["part_number"] for r in data["results"]], ["0100"])
        self.assertEqual(data["count"], 1)

    def test_discontinued_parts_are_included_by_default(self):
        Part.objects.filter(part_number="0347").update(status=Part.Status.DISCONTINUED)
        self.assertEqual(self.numbers("keyboard"), ["0100", "0347"])
