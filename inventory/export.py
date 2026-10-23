"""CSV snapshot of the whole parts list, for other institutions to seed from.

Stock counts are deliberately absent: they describe this lab's shelves, not
the part. Prices are the latest observation per part.

`external_href` mirrors ioref-web's catalog/templatetags/links.py. The two
repositories do not share code, so the rule lives in both places and the
tests here pin the same cases.
"""

import csv
import re

from django.db.models import Prefetch
from django.http import HttpResponse
from django.utils import timezone

from .models import Part, PriceObservation

SAFE_SCHEMES = {"http", "https", "mailto", "tel"}
SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
HOSTNAME = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d+)?")
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def external_href(value):
    url = (value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url[0] in "/#?":
        return url

    scheme = SCHEME.match(url)
    if scheme and scheme.group(1).lower() in SAFE_SCHEMES:
        return url

    host = url.split("/", 1)[0]
    if HOSTNAME.fullmatch(host):
        return "https://" + url
    return ""


def csv_safe(value):
    """Stop a spreadsheet from running a cell as a formula on import."""
    if isinstance(value, str) and value.startswith(FORMULA_TRIGGERS):
        return "'" + value
    return value


def _money(price):
    if price is None:
        return ""
    return format(price.normalize(), "f")


def _row(part):
    observation = part.latest_price
    return [
        part.part_number,
        part.short_name,
        part.description,
        part.label_text,
        str(part.location) if part.location else "",
        part.status,
        _money(observation.price) if observation else "",
        observation.observed_at.date().isoformat() if observation else "",
        part.unit,
        observation.supplier if observation else "",
        external_href(observation.purchase_link) if observation else "",
        part.min_quantity if part.min_quantity is not None else "",
        part.max_quantity if part.max_quantity is not None else "",
        part.manufacturer,
        part.manufacturer_part_num,
        part.supplier_part_num,
        part.group.name if part.group else "",
        part.category.name if part.category else "",
    ]


HEADER = [
    "Part number",
    "Part name",
    "Description",
    "Label text",
    "Location",
    "Status",
    "Price",
    "Price observed",
    "Unit",
    "Vendor",
    "Purchase URL",
    "Minimum",
    "Maximum",
    "Manufacturer",
    "Manufacturer part number",
    "Supplier part number",
    "Group",
    "Category",
]


def export_queryset():
    return Part.objects.select_related("location", "group", "category").prefetch_related(
        Prefetch(
            "price_observations",
            queryset=PriceObservation.objects.order_by("-observed_at", "-id"),
        )
    )


def csv_response():
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="ioref-catalog-{timezone.localdate():%Y-%m-%d}.csv"'
    )
    write_inventory_csv(response, export_queryset())
    return response


def write_inventory_csv(stream, parts):
    writer = csv.writer(stream)
    writer.writerow(HEADER)
    for part in parts:
        writer.writerow([csv_safe(cell) for cell in _row(part)])
