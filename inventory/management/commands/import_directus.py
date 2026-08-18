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

# Location values that describe a use rather than a kind of part, mapped to
# the tag they become. The bin is the only place this source records a use, so
# it is the only place a use can come from.
#
# Keyed on the whole location for the colon-free values and on the fine half
# for "Input: Touch". "soldering bench" becomes "soldering" because the tag
# names the activity, not the furniture.
USE_AS_TAG = {
    "touch": "touch",
    "lending": "lending",
    "tool box": "tool box",
    "soldering bench": "soldering",
}

# Head nouns that are written as acronyms, so the group reads "LEDs" and not
# "Leds". Everything else is title-cased and naively pluralised.
ACRONYMS = {
    "led",
    "ic",
    "usb",
    "lcd",
    "pcb",
    "rfid",
    "ir",
    "uv",
    "mosfet",
    "bjt",
    "smd",
}

# Head nouns too generic to be a useful heading on their own. A page called
# "Sensors" would cover 40 unrelated parts, which is no more helpful than no
# grouping at all, so the qualifier comes with them: "Light sensors".
#
# "pen", "tip", "station" and "hand" are here because the bench tools collide
# with ordinary objects on the head noun alone: a soldering flux pen and a
# ballpoint pen are both "pen", which grouped them together and told a browser
# they were the same kind of thing.
TOO_GENERIC = {
    "sensor",
    "module",
    "kit",
    "assortment",
    "set",
    "part",
    "piece",
    "item",
    "pen",
    "tip",
    "station",
    "hand",
}

# Words that are never what a part *is*. A name ending in one of these has a
# trailing modifier rather than a head: "stranded wire, yellow" is a wire, and
# "sewing pins, box of 80" is a pin. Reaching one means backing off to the
# previous comma-separated segment and trying again.
NOT_A_KIND = {
    # grammatical debris from trailing phrases
    "of",
    "and",
    "or",
    "with",
    "for",
    "the",
    "a",
    "an",
    "to",
    "in",
    "on",
    # colours
    "black",
    "white",
    "red",
    "blue",
    "green",
    "yellow",
    "orange",
    "purple",
    "brown",
    "grey",
    "gray",
    "silver",
    "gold",
    "clear",
    # vague qualifiers
    "assorted",
    "misc",
    "miscellaneous",
    "purpose",
    "use",
    "new",
    "old",
    "spare",
    "large",
    "small",
    "medium",
    "long",
    "short",
    # positional, not a kind: a "phone plug, in-line" is a plug
    "in-line",
    "inline",
    "right-angle",
    "panel-mount",
}

# Collectives describing how a part is packaged, not what it is. Walking past
# them surfaces the real head: "twisted wire pair" is a wire, "DIP switch
# variety" is a switch.
COLLECTIVE = {
    "pair",
    "variety",
    "pack",
    "lot",
    "bundle",
    "roll",
    "spool",
    "bag",
    "box",
}


# Singular nouns that end in "s". Stripping it leaves a stump such as "len",
# which then pluralises back into a different word than the source used and
# names the group "Lens" when it should read "Lenses".
SINGULAR_IN_S = {
    "lens",
    "gas",
    "bus",
    "brass",
    "glass",
    "status",
    "axis",
    "chassis",
    "bias",
    "canvas",
    "cross",
    "truss",
}


def _singular(word):
    if word in SINGULAR_IN_S or word.endswith("ss") or not word.endswith("s"):
        return word
    return word[:-1]


def _candidate_head(segment):
    """The head noun of one comma-separated segment, or None."""
    # Hyphens bind, so "wi-fi module" is not a "fi module" and a "t-square" is
    # not a "square". The cost is that "push-button" parts sit apart from
    # "button" ones, which is a fair reading of the names.
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", segment)
    # Walk back past trailing numbers and units: "hookup wire 22awg" is a wire.
    while words and re.fullmatch(r"[0-9]+[a-z-]*", words[-1]):
        words.pop()
    while len(words) > 1 and _singular(words[-1]) in COLLECTIVE:
        words.pop()
    if not words:
        return None
    head = _singular(words[-1])
    if head in TOO_GENERIC and len(words) > 1:
        return f"{words[-2]} {head}"
    return head


def _is_kind(head):
    """Whether a candidate head names a kind of thing.

    Rejects trailing modifiers, and anything carrying a digit -- those are
    model and part numbers ("PN2222", "ADPS-9960", "V-155-1C25"), which name
    one specific product rather than a class of them.
    """
    last = head.split()[-1]
    return last not in NOT_A_KIND and not any(c.isdigit() for c in head)


def head_noun(name):
    """What kind of thing a part is, taken from its own name.

    In English compounds the head is the last word: a "linear soft
    potentiometer" is a potentiometer, a "potentiometer knob" is a knob. Unlike
    a bin name, that is a property of the part rather than of where it happens
    to be stored.

    Names in this source carry three kinds of trailing noise, all stripped
    before the head is read: parenthetical quantities ("(pack of 25)"), status
    text written into the name ("OBSOLETED: USE PART 1533"), and comma-
    separated modifiers ("Thread, all purpose, spool, black"). The last is
    handled by backing off a segment at a time until a head that names a kind
    of thing appears, which is why the modifier lists above are needed.
    """
    text = re.sub(r"\(.*?\)", " ", (name or "").lower())
    text = re.split(r"\bobsolete", text)[0]
    segments = [s for s in text.split(",") if s.strip()]
    while segments:
        head = _candidate_head(" ".join(segments))
        if head is None:
            return None
        if _is_kind(head):
            return head
        segments.pop()
    return None


def _plural(word):
    """Enough English to name a group without reading as a typo.

    Naive +s gives "Switchs" and "Bushs", which look like bugs to anyone
    browsing the list.
    """
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and not word.endswith(("ay", "ey", "iy", "oy", "uy")):
        return word[:-1] + "ies"
    return word + "s"


# Heads whose conventional spelling neither the acronym list nor plain
# capitalisation produces.
# The source spells it "Wi-Fi", "WiFi" and "wifi". build_groups folds the
# hyphenated spelling into the solid one, so the solid form is what needs a
# conventional label here.
NAME_OVERRIDES = {"wi-fi": "Wi-Fi", "wifi": "Wi-Fi"}


def group_name(head):
    """Turn a head noun into a group heading: "led" -> "LEDs"."""
    words = head.split()
    out = []
    for i, word in enumerate(words):
        is_last = i == len(words) - 1
        if word in NAME_OVERRIDES:
            out.append(NAME_OVERRIDES[word] + ("s" if is_last else ""))
        elif word in ACRONYMS:
            # Upper-cased in place. Capitalising the whole string afterwards
            # would undo this and yield "Leds", which is what it used to do.
            out.append(word.upper() + ("s" if is_last else ""))
        else:
            out.append(_plural(word) if is_last else word)
    name = " ".join(out)
    return name[0].upper() + name[1:] if name else name


def build_groups(rows):
    """Head noun -> group name, for the head nouns worth having a group.

    A head noun used by only one part is not a group -- there is nothing to
    collect -- and the handful of generic ones would collect things that have
    nothing to do with each other.
    """
    counts = Counter()
    for row in rows:
        head = head_noun(row.get("name"))
        if head:
            counts[head] += 1

    # "push-button" and "pushbutton" are the same head spelled two ways, and
    # binding hyphens makes them two groups. Fold the hyphenated spelling into
    # the solid one where both occur, so the split is not an artefact of
    # punctuation in the source names.
    aliases = {}
    for head in [h for h in counts if "-" in h]:
        solid = head.replace("-", "")
        if solid in counts:
            counts[solid] += counts.pop(head)
            aliases[head] = solid

    vocabulary = {
        head: group_name(head)
        for head, n in counts.items()
        if n > 1 and head not in TOO_GENERIC
    }
    # Both spellings have to resolve, because head_noun still returns whichever
    # one the part's own name used. They share a slug, so they share a row.
    for alias, solid in aliases.items():
        if solid in vocabulary:
            vocabulary[alias] = vocabulary[solid]
    return vocabulary


def tags_for(location_name):
    """Tags still come from the bin, because a use is what a bin can tell you.

    Both shapes of location carry one. "Input: Touch" puts the use in the fine
    half, while "tool box", "lending" and "soldering bench" are the whole
    value. Reading only the first shape is what left 121 parts untagged: two of
    the entries above never appear after a colon at all.
    """
    if not location_name:
        return []
    _, _, fine = location_name.rpartition(":")
    fine = (fine or location_name).strip().lower()
    tag = USE_AS_TAG.get(fine)
    return [tag] if tag else []


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
    are already dicts. Accepting both shapes stops a str-only assumption
    failing silently and importing no history at all.
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
    out = []
    for key, value in _as_dict(raw).items():
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
        vocabulary = build_groups(rows)
        stats = Counter()
        assigned = {}
        groups = {}

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

            tag_names = tags_for(location_name)
            head = head_noun(row.get("name"))
            group = None
            if head in vocabulary:
                if head not in groups:
                    name = vocabulary[head]
                    group_row, _ = Group.objects.get_or_create(
                        slug=slugify(name), defaults={"name": name}
                    )
                    # get_or_create matches on slug and leaves defaults alone
                    # for a row that already exists, so a renamed heading would
                    # never reach the database: "LEDs" slugifies to the "leds"
                    # that "Leds" already claimed.
                    if group_row.name != name:
                        group_row.name = name
                        group_row.save(update_fields=["name"])
                    groups[head] = group_row
                group = groups[head]
            else:
                stats["ungrouped"] += 1

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

            if group:
                assigned[number] = group.slug

            stats["stock_events"] += self._import_stock(part, row)
            stats["price_observations"] += self._import_prices(part, row)

        if not options["dry_run"]:
            # Written back beside the export so ioref-web can set a component
            # page's inventory_group without duplicating these rules. Inventory
            # owns the derivation; the frontdoor just reads the answer.
            mapping = export / "part_groups.json"
            mapping.write_text(json.dumps(assigned, indent=1, sort_keys=True))
            self.stdout.write(f"wrote {mapping} ({len(assigned)} parts)")

            # The vocabulary is derived, so a re-run with improved derivation
            # leaves the groups it no longer produces behind, holding no parts.
            # Without this the import does not converge: correcting "Pens" into
            # "Iron tips" would keep both, and the empty one would still be
            # offered as a heading. Only groups the import owns are considered,
            # which is all of them -- nothing else creates a Group.
            emptied = Group.objects.filter(parts__isnull=True)
            stats["groups_pruned"] = emptied.count()
            emptied.delete()

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
                        part=part,
                        kind=kind,
                        quantity=quantity,
                        observed_at=when,
                        note="imported",
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
        part.price_observations.filter(
            recorded_by__isnull=True, note="imported"
        ).delete()

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
                    part=part,
                    price=amount,
                    currency="USD",
                    supplier=(suppliers.get(key) or "")[:200],
                    purchase_link=(links.get(key) or "")[:1000],
                    observed_at=when,
                    note="imported",
                )
            )
        PriceObservation.objects.bulk_create(observations)
        return len(observations)

    def _report(self, stats, total):
        self.stdout.write(f"\nRead {total} source rows")
        for key in (
            "created",
            "updated",
            "would_import",
            "skipped_no_number",
            "locations_created",
            "stock_events",
            "price_observations",
            "groups_pruned",
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
