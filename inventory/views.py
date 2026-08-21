"""Public read-only browsing of the inventory.

This exists so the application is useful on its own. ioref-web renders a
branded version of the same data over the API, but a deployment with nothing in
front of it, another organization running this by itself, still needs a way
to look at its own stock.

Deliberately unstyled in anyone's house colors. CMU-specific presentation
belongs in ioref-web; this is neutral so it does not look wrong elsewhere.

Read-only and safe for anonymous access, with one exception: prices and
suppliers are shown only to signed-in staff. Stock levels answer "do you have
any, and where"; what it cost and who sold it is procurement's business.
"""

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import Group, Location, Part, StockEvent

PAGE_SIZE = 100


def _latest_quantity(kind):
    return Subquery(
        StockEvent.objects.filter(part=OuterRef("pk"), kind=kind)
        .order_by("-observed_at", "-id")
        .values("quantity")[:1]
    )


def _browsable():
    if not settings.PUBLIC_BROWSE:
        raise Http404("Public browsing is disabled on this deployment.")


def _may_see_costs(request):
    return request.user.is_authenticated


def part_list(request):
    _browsable()

    search = request.GET.get("q", "").strip()
    location = request.GET.get("location", "").strip()
    group = request.GET.get("group", "").strip()
    show = request.GET.get("show", "").strip()

    # See CLAUDE.md: the annotations cannot be named on_floor/in_backstock,
    # which are read-only properties that setattr() cannot write through.
    parts = (
        Part.objects.select_related("location", "group")
        .prefetch_related("tags")
        .annotate(
            _ann_on_floor=_latest_quantity(StockEvent.Kind.INVENTORY),
            _ann_in_backstock=_latest_quantity(StockEvent.Kind.BACKSTOCK),
        )
        .exclude(status=Part.Status.DISCONTINUED)
    )

    if search:
        parts = parts.filter(
            Q(short_name__icontains=search)
            | Q(description__icontains=search)
            | Q(part_number__icontains=search)
        )
    if location:
        parts = parts.filter(location__code=location)
    if group:
        parts = parts.filter(group__slug=group)
    if show == "low":
        parts = parts.annotate(
            _total=Coalesce(F("_ann_on_floor"), Value(0))
            + Coalesce(F("_ann_in_backstock"), Value(0))
        ).filter(min_quantity__isnull=False, _total__lt=F("min_quantity"))

    paginator = Paginator(parts, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "inventory/part_list.html",
        {
            "page_obj": page,
            "parts": page.object_list,
            "total": paginator.count,
            "search": search,
            "location": location,
            "group": group,
            "show": show,
            "groups": Group.objects.filter(parts__isnull=False).distinct(),
            # Only locations holding something, so the filter is not a wall of
            # empty bins.
            "locations": Location.objects.filter(
                is_active=True, parts__isnull=False
            ).distinct(),
            "may_see_costs": _may_see_costs(request),
        },
    )


def part_detail(request, part_number):
    _browsable()

    part = get_object_or_404(
        Part.objects.select_related("location", "group").prefetch_related("tags"),
        part_number=part_number,
    )
    may_see_costs = _may_see_costs(request)

    return render(
        request,
        "inventory/part_detail.html",
        {
            "part": part,
            "counts": part.stock_events.select_related("recorded_by")[:20],
            # Querying prices at all is skipped for anonymous visitors rather
            # than fetched and hidden in the template.
            "prices": part.price_observations.all()[:10] if may_see_costs else None,
            "may_see_costs": may_see_costs,
        },
    )
