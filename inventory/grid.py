"""Server side of the spreadsheet bulk editor. Every write goes through inventory.bulk."""

import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .bulk import EDITABLE, BulkEditError, apply, apply_values, plan, recent_batches, undo_last, undoable_batches
from .models import Location, Part

COLUMNS = (
    "short_name",
    "description",
    "label_text",
    "status",
    "unit",
    "location",
    "min_quantity",
    "max_quantity",
    "manufacturer",
    "manufacturer_part_num",
    "supplier_part_num",
)


def _cell(part, column):
    if column == "location":
        return part.location.code if part.location else ""
    value = getattr(part, column)
    return "" if value is None else value


def _forbidden(request):
    return not request.user.has_perm("inventory.change_part")


@require_GET
def data(request):
    if _forbidden(request):
        return JsonResponse({"error": "Not allowed."}, status=403)
    parts = Part.objects.select_related("group", "location").order_by("part_number")
    return JsonResponse(
        {
            "columns": COLUMNS,
            "editable": sorted(EDITABLE),
            "choices": {
                "status": [value for value, _ in Part.Status.choices],
                "location": list(Location.objects.order_by("code").values_list("code", flat=True)),
                "unit": list(
                    Part.objects.exclude(unit="").order_by("unit").values_list("unit", flat=True).distinct()
                ),
            },
            "rows": [
                {
                    "id": part.pk,
                    "part_number": part.part_number,
                    "group": part.group.name if part.group else "",
                    **{column: _cell(part, column) for column in COLUMNS},
                }
                for part in parts
            ],
        }
    )


def _request_body(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get("ids"), list):
        return None
    return body


@require_POST
def preview(request):
    if _forbidden(request):
        return JsonResponse({"error": "Not allowed."}, status=403)
    body = _request_body(request)
    if body is None:
        return JsonResponse({"error": "Malformed request."}, status=400)
    try:
        result = plan(body["ids"], str(body.get("field") or ""), str(body.get("value") or ""))
    except BulkEditError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(
        {
            "count": len(result.rows),
            "rows": [
                {"part_number": part.part_number, "old": old, "new": new}
                for part, old, new in result.rows
            ],
            "errors": result.errors,
        }
    )


@require_POST
def apply_edit(request):
    if _forbidden(request):
        return JsonResponse({"error": "Not allowed."}, status=403)
    body = _request_body(request)
    if body is None:
        return JsonResponse({"error": "Malformed request."}, status=400)
    try:
        batch, count = apply(
            body["ids"],
            str(body.get("field") or ""),
            str(body.get("value") or ""),
            request.user,
            expect=body.get("expect"),
        )
    except BulkEditError as exc:
        return JsonResponse({"error": str(exc), "errors": exc.errors}, status=400)
    return JsonResponse({"batch": str(batch), "count": count})


@require_GET
def undo_status(request):
    if _forbidden(request):
        return JsonResponse({"error": "Not allowed."}, status=403)
    stack = undoable_batches()
    return JsonResponse(
        {
            "depth": len(stack),
            "next": None
            if not stack
            else {
                "batch": str(stack[0]["batch"]),
                "field": stack[0]["field"],
                "count": stack[0]["count"],
                "changed_at": stack[0]["changed_at"].isoformat(),
            },
        }
    )


@require_POST
def undo(request):
    if _forbidden(request):
        return JsonResponse({"error": "Not allowed."}, status=403)
    try:
        batch, count = undo_last(request.user)
    except BulkEditError as exc:
        return JsonResponse({"error": str(exc), "errors": exc.errors}, status=400)
    return JsonResponse({"batch": str(batch), "count": count})


@require_POST
def cell(request):
    if _forbidden(request):
        return JsonResponse({"error": "Not allowed."}, status=403)
    body = _request_body_for_cell(request)
    if body is None:
        return JsonResponse({"error": "Malformed request."}, status=400)
    try:
        batch, count = apply_values({body["id"]: body["value"]}, body["field"], request.user)
    except BulkEditError as exc:
        return JsonResponse({"error": str(exc), "errors": exc.errors}, status=400)
    return JsonResponse({"batch": str(batch), "count": count})


def _request_body_for_cell(request):
    try:
        body = json.loads(request.body)
        return {
            "id": int(body["id"]),
            "field": str(body["field"]),
            "value": str(body.get("value") or ""),
        }
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


@require_GET
def history(request):
    if _forbidden(request):
        return JsonResponse({"error": "Not allowed."}, status=403)
    return JsonResponse(
        {
            "batches": [
                {
                    "batch": str(row["batch"]),
                    "field": row["field"],
                    "count": row["count"],
                    "changed_at": row["changed_at"].isoformat(),
                    "by": row["by"] or "",
                    "changes": row["changes"],
                    "undone": row["undone"],
                    "is_undo": row["is_undo"],
                }
                for row in recent_batches()
            ]
        }
    )
