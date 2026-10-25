"""Quick inventory: count one part at a time, with a one-step undo.

Counts are stock events, which are append-only. Undo voids the entry instead
of deleting it, and voided counts stop counting everywhere (see
StockEventQuerySet.active).
"""

import json

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Part, StockEvent

KINDS = (("inventory", StockEvent.Kind.INVENTORY), ("backstock", StockEvent.Kind.BACKSTOCK))


class QuickError(Exception):
    pass


def _allowed(request):
    return request.user.has_perm("inventory.add_stockevent")


def _unit(part, quantity):
    return "" if part.unit == "each" else f" {part.unit}"


def _describe(part, event):
    return {
        "id": event.pk,
        "kind": event.kind,
        "quantity": event.quantity,
        "text": f"{event.quantity}{_unit(part, event.quantity)}",
        "observed_at": event.observed_at.isoformat(),
    }


def lookup_part(part_number):
    part = Part.objects.select_related("location").get(part_number=part_number)
    latest = {}
    for name, kind in KINDS:
        event = part.stock_events.active().filter(kind=kind).order_by("-observed_at", "-id").first()
        latest[name] = _describe(part, event) if event else None
    return {
        "part_number": part.part_number,
        "short_name": part.short_name,
        "description": part.description,
        "location": str(part.location) if part.location else "",
        "unit": part.unit,
        "inventory": latest["inventory"],
        "backstock": latest["backstock"],
    }


def _whole(raw, label):
    raw = (raw or "").strip()
    if raw == "":
        return None
    if not raw.isdigit():
        raise QuickError(f"{label} must be a whole number.")
    return int(raw)


def record_counts(part_number, inventory, backstock, user):
    part = Part.objects.get(part_number=part_number)
    counts = {
        StockEvent.Kind.INVENTORY: _whole(inventory, "Inventory"),
        StockEvent.Kind.BACKSTOCK: _whole(backstock, "Backstock"),
    }
    counts = {kind: qty for kind, qty in counts.items() if qty is not None}
    if not counts:
        raise QuickError("Enter an inventory or backstock count.")

    now = timezone.now()
    with transaction.atomic():
        events = [
            StockEvent.objects.create(
                part=part, kind=kind, quantity=qty, observed_at=now, recorded_by=user
            )
            for kind, qty in counts.items()
        ]
    return {
        "part_number": part.part_number,
        "short_name": part.short_name,
        "events": [_describe(part, event) for event in events],
    }


def undo_counts(ids, user):
    if not ids:
        raise QuickError("Nothing to undo.")
    events = list(StockEvent.objects.select_related("part").filter(pk__in=ids))
    if len(events) != len(set(ids)):
        raise QuickError("Those counts no longer exist.")
    for event in events:
        if event.recorded_by_id != user.pk:
            raise QuickError("You can only undo counts you recorded.")
        if event.voided_at is not None:
            raise QuickError(f"{event.part.part_number} was already undone.")
        newer = (
            StockEvent.objects.active()
            .filter(part=event.part, kind=event.kind)
            .filter(Q(observed_at__gt=event.observed_at) | Q(observed_at=event.observed_at, id__gt=event.pk))
            .exists()
        )
        if newer:
            raise QuickError(
                f"A newer {event.kind} count for {event.part.part_number} exists, so this one can't be undone here."
            )
    with transaction.atomic():
        now = timezone.now()
        for event in events:
            event.voided_at = now
            event.voided_by = user
            event.save(update_fields=["voided_at", "voided_by"])
    return len(events)


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _body(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


@require_GET
def lookup(request):
    if not _allowed(request):
        return _error("Not allowed.", 403)
    number = (request.GET.get("part_number") or "").strip()
    if not number:
        return _error("Enter a part number.")
    try:
        return JsonResponse(lookup_part(number))
    except Part.DoesNotExist:
        return _error(f"No part numbered {number}.", 404)


@require_POST
def record(request):
    if not _allowed(request):
        return _error("Not allowed.", 403)
    body = _body(request)
    if body is None:
        return _error("Malformed request.")
    try:
        result = record_counts(
            str(body.get("part_number") or "").strip(),
            str(body.get("inventory") or ""),
            str(body.get("backstock") or ""),
            request.user,
        )
    except Part.DoesNotExist:
        return _error("No such part.", 404)
    except QuickError as exc:
        return _error(str(exc))
    return JsonResponse(result)


@require_POST
def undo(request):
    if not _allowed(request):
        return _error("Not allowed.", 403)
    body = _body(request)
    if body is None:
        return _error("Malformed request.")
    try:
        count = undo_counts([int(i) for i in body.get("ids", [])], request.user)
    except (QuickError, TypeError, ValueError) as exc:
        return _error(str(exc) if isinstance(exc, QuickError) else "Malformed request.")
    return JsonResponse({"undone": count})
