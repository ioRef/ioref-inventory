"""What the admin opens on.

Django's index lists every registered model grouped by app label. That
duplicates the sidebar, disagrees with it about both the groupings and the
names, and answers no question anyone actually arrives with. This replaces it
with the three exceptions worth acting on, each one a link into the part list
already filtered to it.

Every number here is restricted to active parts except the uncounted one. Of
the 354 parts below their minimum, 197 are discontinued and 43 are on their way
out: reporting those as work to do would bury the hundred that are real. An
uncounted part is worth seeing whatever its status, because the reason it has
never been counted may be that nobody has looked at it in years.
"""

from django.urls import reverse
from django.utils.http import urlencode

from inventory.models import Part, StockEvent

RECENT_COUNTS = 8


def _part_list(**params):
    """A link into the part changelist, filtered.

    The keys have to be filters PartAdmin accepts. The changelist rejects a
    parameter that is not in its list_filter, so `stock` and `status` work
    because StockStateFilter and the status field are both declared there.
    """
    return f"{reverse('admin:inventory_part_changelist')}?{urlencode(params)}"


def dashboard_callback(request, context):
    parts = Part.objects.with_stock()
    active = parts.filter(status=Part.Status.ACTIVE)

    context["cards"] = [
        {
            "label": "Below minimum",
            "hint": "Active parts under their restock threshold",
            "value": active.below_minimum().count(),
            "url": _part_list(stock="low", status=Part.Status.ACTIVE),
            "tone": "restock",
        },
        {
            "label": "Out of stock",
            "hint": "Active parts counted at zero",
            "value": active.out_of_stock().count(),
            "url": _part_list(stock="zero", status=Part.Status.ACTIVE),
            "tone": "restock",
        },
        {
            "label": "Never counted",
            "hint": "No count has ever been recorded, of any status",
            "value": parts.uncounted().count(),
            "url": _part_list(stock="uncounted"),
            "tone": "unknown",
        },
    ]

    context["recent_counts"] = (
        StockEvent.objects.select_related("part", "recorded_by")
        # Counts imported from the old system carry no recorder and are all
        # dated years back, so they would fill this list and say nothing about
        # what anyone is doing now.
        .filter(recorded_by__isnull=False)
        .order_by("-observed_at", "-id")[:RECENT_COUNTS]
    )

    return context
