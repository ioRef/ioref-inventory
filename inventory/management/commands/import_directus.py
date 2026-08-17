"""Import stock data from a Directus export.

Consumes the JSONL bundle written by tools/directus_to_json.sh, so this app
never needs a MySQL driver or a live MySQL server.

Takes the stock half of the old `parts` collection: counts, prices, suppliers,
locations, minimum and maximum quantities. Guide content -- the seven docs_*
fields, images, categories -- belongs to ioref-web and is deliberately ignored
here, even though it sits in the same source rows.

Idempotent: parts are matched on part_number, and history is rebuilt from the
source rather than appended to, so a re-run converges rather than doubling up.
"""

import datetime
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from inventory.models import Group, Location, Part, PriceObservation, StockEvent, Tag

# The source status column is free text and has drifted: 141 rows are empty,
# both "Discontinue" and "Discontinued" occur, and three encode a substitute
# part in the status itself ("Discontinued (see #4988)").
STATUS_MAP = {
    "active": Part.Status.ACTIVE,
    "currently unavailable": Part.Status.UNAVAILABLE,
    "to discontinue": Part.Status.TO_DISCONTINUE,
    "discontinue": Part.Status.TO_DISCONTINUE,
    "discontinued": Part.Status.DISCONTINUED,
}

# Fine-level location values that describe a use rather than a kind of part.
USE_AS_TAG = {"touch", "lending", "tool box"}

# Singular/plural drift in the same vocabulary.
GROUP_ALIASES = {"potentiometer": "Potentiometers", "capacitor": "Capacitors"}


def parse_status(raw):
    """Map free-text status onto the enum, keeping any parenthetical note."""
    text = (raw or "").strip()
    if not text:
        return Part.Status.ACTIVE, ""
    note = ""
    match = re.match(r"^([^(]+)\((.*)\)\s*$", text)
    if match:
        text, note = match.group(1).strip(), match.group(2).strip()
    return STATUS_MAP.get(text.lower(), Part.Status.ACTIVE), note


def _as_dict(raw):
    """Coerce a history field to a dict.

    These are JSON columns in MySQL, and mysql's JSON_OBJECT() embeds them as
    nested objects rather than strings -- so after parsing an export line they
    are already dicts. Accepting both shapes means the same importer works
    against a JSON-column export and a plain text one, and more importantly
    stops a str-only assumption failing silently and importing no history at all.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_history(raw):
    """Turn a timestamp-keyed JSON object into [(quantity, datetime)].

    Keys are not zero-padded ("2025-2-6 18:21:21"). Values are strings, and the
    legacy "-1" sentinel for "never counted" is dropped rather than imported as
    a quantity -- absence is the correct representation.
    """
    entries = _as_dict(raw)

    out = []
    for key, value in entries.items():
        try:
            when = datetime.datetime.strptime(key.strip(), "%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            continue
        try:
            quantity = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if quantity < 0:
            continue  # The -1 "never counted" sentinel.
        out.append((quantity, timezone.make_aware(when)))
    return sorted(out, key=lambda t: t[1])


def parse_money(raw):
    if raw is None:
        return None
    text = str(raw).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value >= 0 else None


def classify(location_name):
    """Split a legacy location string into (group, tag names).

    Only the fine half is inventory's business. The macro half is a teaching
    taxonomy that ioref-web owns; see Group's docstring.
    """
    if not location_name or ":" not in location_name:
        return None, []
    _, _, fine = location_name.partition(":")
    fine = fine.strip()
    if not fine:
        return None, []
    if fine.lower() in USE_AS_TAG:
        return None, [fine.lower()]
    name = GROUP_ALIASES.get(fine.lower(), fine)
    group, _ = Group.objects.get_or_create(slug=slugify(name), defaults={"name": name})
    return group, []


class Command(BaseCommand):
    help = "Import stock data from a Directus JSONL export."

    def add_arguments(self, parser):
        parser.add_argument("export_dir", type=Path)
        parser.add_argument(
            "--dry-run", action="store_true", help="Report without writing."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        export = options["export_dir"]
        parts_file = export / "parts.jsonl"
        if not parts_file.exists():
            raise CommandError(f"No parts.jsonl in {export}")

        rows = [json.loads(line) for line in parts_file.open(encoding="utf-8")]
        stats = Counter()

        for row in rows:
            number = (row.get("part_number") or "").strip()
            if not number:
                stats["skipped_no_number"] += 1
                continue

            location_name = (row.get("location") or "").strip()
            status, status_note = parse_status(row.get("status"))

            if options["dry_run"]:
                # Nothing is created, not even rolled back: a dry run that
                # writes and then reverts still reports inflated counts.
                stats["would_import"] += 1
                continue

            location = None
            if location_name:
                location, created = Location.objects.get_or_create(code=location_name)
                stats["locations_created"] += int(created)

            group, tag_names = classify(location_name)

            description = (row.get("description") or "").strip()
            if status_note:
                # Keep "see #4988" rather than discarding it with the status.
                description = f"{description}\n\n[{status_note}]".strip()

            part, created = Part.objects.update_or_create(
                part_number=number,
                defaults={
                    "short_name": (row.get("name") or number)[:200],
                    "description": description,
                    "label_text": (row.get("label_text") or "")[:200],
                    "status": status,
                    "unit": (row.get("unit") or "each")[:32],
                    "group": group,
                    "location": location,
                    "min_quantity": _positive_int(row.get("min_quantity")),
                    "max_quantity": _positive_int(row.get("max_quantity")),
                    "supplier_part_num": (row.get("supplier_part_num") or "")[:128],
                },
            )
            stats["created" if created else "updated"] += 1

            if tag_names:
                part.tags.set(
                    Tag.objects.get_or_create(slug=slugify(t), defaults={"name": t})[0]
                    for t in tag_names
                )

            stats["stock_events"] += self._import_stock(part, row)
            stats["price_observations"] += self._import_prices(part, row)

        self._report(stats, len(rows))

        if options["dry_run"]:
            transaction.set_rollback(True)

    def _import_stock(self, part, row):
        # Rebuilt rather than appended to, so re-running converges. Safe because
        # imported events carry no recorded_by -- nothing entered by a human
        # through the admin is touched.
        part.stock_events.filter(recorded_by__isnull=True, note="imported").delete()
        events = []
        for kind, field in (
            (StockEvent.Kind.INVENTORY, "inventory_history"),
            (StockEvent.Kind.BACKSTOCK, "backstock_history"),
        ):
            for quantity, when in parse_history(row.get(field)):
                events.append(
                    StockEvent(
                        part=part, kind=kind, quantity=quantity,
                        observed_at=when, note="imported",
                    )
                )
        StockEvent.objects.bulk_create(events)
        return len(events)

    def _import_prices(self, part, row):
        """Rejoin price, supplier and purchase link on their shared timestamp.

        The source kept three parallel objects keyed by time, so a price could
        only be tied to its supplier when all three happened to be edited in the
        same request. Matching on the key recovers the association where it
        exists and leaves the field blank where it does not.
        """
        part.price_observations.filter(recorded_by__isnull=True, note="imported").delete()

        prices = _keyed(row.get("price_history"))
        suppliers = _keyed(row.get("supplier_history"))
        links = _keyed(row.get("purchase_link_history"))

        observations = []
        for key, raw_price in prices.items():
            amount = parse_money(raw_price)
            if amount is None:
                continue
            try:
                when = timezone.make_aware(
                    datetime.datetime.strptime(key.strip(), "%Y-%m-%d %H:%M:%S")
                )
            except (ValueError, AttributeError):
                continue
            observations.append(
                PriceObservation(
                    part=part, price=amount, currency="USD",
                    supplier=(suppliers.get(key) or "")[:200],
                    purchase_link=(links.get(key) or "")[:1000],
                    observed_at=when, note="imported",
                )
            )
        PriceObservation.objects.bulk_create(observations)
        return len(observations)

    def _report(self, stats, total):
        self.stdout.write(f"\nRead {total} source rows")
        for key in (
            "created", "updated", "would_import", "skipped_no_number",
            "locations_created", "stock_events", "price_observations",
        ):
            if stats[key]:
                self.stdout.write(f"  {key.replace('_', ' '):<22} {stats[key]:>7}")
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{Part.objects.count()} parts, {Group.objects.count()} groups, "
                f"{Tag.objects.count()} tags, {Location.objects.count()} locations, "
                f"{StockEvent.objects.count()} stock events, "
                f"{PriceObservation.objects.count()} prices."
            )
        )


def _positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _keyed(raw):
    return _as_dict(raw)
