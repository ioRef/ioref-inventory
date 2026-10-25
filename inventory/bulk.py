"""Bulk edits to part fields, shared by every interface that offers them.

An interface only chooses parts and a value. Everything that decides whether a
change is allowed happens here: the field allowlist, per-part validation, and
the all-or-nothing write with its change log.
"""

import uuid
from dataclasses import dataclass, field as dataclass_field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max

from .models import Location, Part, PartChange

FK_FIELDS = {"location": (Location, "code")}
INT_FIELDS = ("min_quantity", "max_quantity")
QUANTITY_FIELDS = INT_FIELDS
TEXT_FIELDS = (
    "short_name",
    "description",
    "label_text",
    "status",
    "unit",
    "manufacturer",
    "manufacturer_part_num",
    "supplier_part_num",
)
REQUIRED = {"short_name", "status", "unit"}
EDITABLE = frozenset(FK_FIELDS) | frozenset(INT_FIELDS) | frozenset(TEXT_FIELDS)


class BulkEditError(Exception):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or {}


@dataclass
class Plan:
    field: str
    rows: list = dataclass_field(default_factory=list)
    errors: dict = dataclass_field(default_factory=dict)

    @property
    def ok(self):
        return not self.errors


def _key(part, field):
    """The value as the user would type it, so it can be typed back in to undo."""
    if field in FK_FIELDS:
        obj = getattr(part, field)
        return "" if obj is None else getattr(obj, FK_FIELDS[field][1])
    value = getattr(part, field)
    return "" if value is None else str(value)


def _coerce(field, raw):
    raw = (raw or "").strip()
    if field in FK_FIELDS:
        if raw == "":
            return None
        model, key = FK_FIELDS[field]
        try:
            return model.objects.get(**{key: raw})
        except model.DoesNotExist:
            raise BulkEditError(f"No {field} with {key} {raw!r}.")
    if field in INT_FIELDS:
        if raw == "":
            return None
        if not raw.isdigit():
            raise BulkEditError(f"{field} must be a whole number.")
        return int(raw)
    if raw == "" and field in REQUIRED:
        raise BulkEditError(f"{field} cannot be blank.")
    return raw


def _check_field(field):
    if field == "part_number":
        raise BulkEditError("Part numbers cannot be changed.")
    if field not in EDITABLE:
        raise BulkEditError(f"{field!r} cannot be bulk edited.")


def plan_values(assignments, field):
    """What setting each part's `field` to its own raw value would do. Writes nothing.

    `assignments` maps part pk to the value to set, as the user would type it.
    """
    _check_field(field)
    parts = list(
        Part.objects.filter(pk__in=assignments).select_related("group", "category", "location")
    )
    if len(parts) != len(assignments):
        raise BulkEditError("Some selected parts no longer exist. Reload and try again.")

    coerced = {}
    result = Plan(field=field)
    for part in parts:
        raw = assignments[part.pk]
        if raw not in coerced:
            coerced[raw] = _coerce(field, raw)
        old = _key(part, field)
        setattr(part, field, coerced[raw])
        try:
            part.full_clean(exclude=[f.name for f in Part._meta.fields if f.name != field])
            # A maximum of 0 is how the legacy spreadsheet recorded "no maximum".
            if (
                field in QUANTITY_FIELDS
                and part.min_quantity is not None
                and part.max_quantity
                and part.min_quantity > part.max_quantity
            ):
                raise ValidationError("Minimum is greater than maximum.")
        except ValidationError as exc:
            result.errors[part.part_number] = "; ".join(exc.messages)
            continue
        result.rows.append((part, old, _key(part, field)))
    return result


def plan(part_ids, field, raw):
    return plan_values({pk: raw for pk in set(part_ids)}, field)


def apply_values(assignments, field, user, expect=None, reverts=None):
    """Set `field` on each part, or on none. Returns (batch id, count)."""
    result = plan_values(assignments, field)
    if not result.ok:
        raise BulkEditError(
            f"{len(result.errors)} part(s) would not validate. Nothing was changed.",
            errors=result.errors,
        )
    if expect is not None and expect != len(result.rows):
        raise BulkEditError(
            f"Expected {expect} changes but this edit makes {len(result.rows)}. "
            "Nothing was changed."
        )

    batch = uuid.uuid4()
    with transaction.atomic():
        for part, old, new in result.rows:
            part.save(update_fields=[field, "updated_at"])
            PartChange.objects.create(
                part=part,
                field=field,
                old_value=old,
                new_value=new,
                batch=batch,
                changed_by=user,
                reverts=reverts,
            )
    return batch, len(result.rows)


def apply(part_ids, field, raw, user, expect=None):
    return apply_values({pk: raw for pk in set(part_ids)}, field, user, expect=expect)


UNDO_DEPTH = 5


def undoable_batches(limit=UNDO_DEPTH):
    """Newest first. A batch is undoable until it is undone, and undo batches are never undone."""
    undo_rows = PartChange.objects.exclude(reverts=None)
    reverted = set(undo_rows.values_list("reverts", flat=True))
    undo_batches = set(undo_rows.values_list("batch", flat=True))
    recent = (
        PartChange.objects.values("batch", "field")
        .annotate(changed_at=Max("changed_at"), count=Count("id"))
        .order_by("-changed_at")
    )
    found = []
    for row in recent:
        if row["batch"] in reverted or row["batch"] in undo_batches:
            continue
        found.append(row)
        if len(found) == limit:
            break
    return found


def undo_last(user):
    """Put back the values of the newest undoable batch. Returns (batch, count)."""
    top = undoable_batches(limit=1)
    if not top:
        raise BulkEditError("Nothing to undo.")
    batch, field = top[0]["batch"], top[0]["field"]
    changes = PartChange.objects.filter(batch=batch).select_related("part")
    assignments = {change.part_id: change.old_value for change in changes}
    return apply_values(assignments, field, user, expect=len(assignments), reverts=batch)


HISTORY_CHANGES = 500


def recent_batches(limit=10):
    """Newest first. Each batch with its parts, and whether it has been undone."""
    undo_rows = PartChange.objects.exclude(reverts=None)
    reverted = set(undo_rows.values_list("reverts", flat=True))
    undo_batches = set(undo_rows.values_list("batch", flat=True))
    rows = (
        PartChange.objects.values("batch", "field")
        .annotate(changed_at=Max("changed_at"), count=Count("id"), by=Max("changed_by__username"))
        .order_by("-changed_at")[:limit]
    )
    result = []
    for row in rows:
        changes = [
            {"part": part, "name": name, "old": old_value, "new": new_value}
            for part, name, old_value, new_value in PartChange.objects.filter(batch=row["batch"])
            .order_by("part__part_number")
            .values_list("part__part_number", "part__short_name", "old_value", "new_value")[:HISTORY_CHANGES]
        ]
        result.append(
            {
                **row,
                "changes": changes,
                "undone": row["batch"] in reverted,
                "is_undo": row["batch"] in undo_batches,
            }
        )
    return result
