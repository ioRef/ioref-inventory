from django.contrib import admin
from django.db.models import OuterRef, Subquery
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import RangeDateFilter

from .models import Group, Location, Part, PriceObservation, StockEvent, Tag


def _latest_quantity(kind):
    return Subquery(
        StockEvent.objects.filter(part=OuterRef("pk"), kind=kind)
        .order_by("-observed_at", "-id")
        .values("quantity")[:1]
    )


class StockEventInline(TabularInline):
    model = StockEvent
    extra = 0
    fields = ("kind", "quantity", "observed_at", "recorded_by", "note")
    readonly_fields = ("recorded_by",)
    ordering = ("-observed_at",)
    # History is append-only: corrections are made by adding a newer count, so
    # the record of what was believed and when survives.
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False


class PriceObservationInline(TabularInline):
    model = PriceObservation
    extra = 0
    fields = ("price", "currency", "supplier", "purchase_link", "observed_at", "note")
    ordering = ("-observed_at",)
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Part)
class PartAdmin(ModelAdmin):
    list_display = (
        "part_number",
        "short_name",
        "group",
        "status",
        "location",
        "on_floor",
        "in_backstock",
        "stock_state",
    )
    list_filter = ("status", "group", "tags", "location")
    search_fields = ("part_number", "short_name", "description", "supplier_part_num")
    autocomplete_fields = ("group", "tags")
    inlines = (StockEventInline, PriceObservationInline)
    list_select_related = ("location", "group")

    def get_queryset(self, request):
        # Without these annotations the on_floor/in_backstock/stock_state columns
        # each hit the database per row, so a 100-row page costs ~200 extra
        # queries. The properties read the annotations back when present.
        return (
            super()
            .get_queryset(request)
            .annotate(
                _ann_on_floor=_latest_quantity(StockEvent.Kind.INVENTORY),
                _ann_in_backstock=_latest_quantity(StockEvent.Kind.BACKSTOCK),
            )
        )

    @admin.display(description="Stock", ordering="_ann_on_floor")
    def stock_state(self, part):
        """Total on hand, flagged only when it needs attention.

        A healthy row is just a number. Marking every row -- including the
        ~90% that are fine -- makes the list harder to scan, not easier, and
        buries the handful that actually need restocking.
        """
        total = part.total_on_hand

        if total is None:
            return format_html(
                '<span class="stock-flag stock-flag--unknown" data-flag="uncounted"'
                ' title="No count has ever been recorded">—</span>'
            )

        if part.needs_restock:
            return format_html(
                '<span class="stock-flag stock-flag--restock" data-flag="low"'
                ' title="Below the minimum of {}">{}</span>',
                part.min_quantity,
                total,
            )

        return format_html('<span class="stock-value">{}</span>', total)

    class Media:
        css = {"all": ("inventory/admin.css",)}
        # Widens the object link's hit area to the whole row.
        js = ("inventory/admin.js",)

    def save_formset(self, request, form, formset, change):
        # Attribute counts entered through the admin to whoever typed them.
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, (StockEvent, PriceObservation)) and not instance.pk:
                instance.recorded_by = request.user
            instance.save()
        formset.save_m2m()


@admin.register(Location)
class LocationAdmin(ModelAdmin):
    list_display = ("code", "name", "is_active", "part_count")
    list_filter = ("is_active",)
    search_fields = ("code", "name")

    @admin.display(description="Parts")
    def part_count(self, location):
        return location.parts.count()


@admin.register(StockEvent)
class StockEventAdmin(ModelAdmin):
    list_display = ("part", "kind", "quantity", "observed_at", "recorded_by")
    list_filter = ("kind", ("observed_at", RangeDateFilter))
    search_fields = ("part__part_number", "part__short_name")
    date_hierarchy = "observed_at"
    list_select_related = ("part", "recorded_by")


@admin.register(PriceObservation)
class PriceObservationAdmin(ModelAdmin):
    list_display = ("part", "price", "currency", "supplier", "observed_at")
    list_filter = ("currency", "supplier")
    search_fields = ("part__part_number", "part__short_name", "supplier")
    date_hierarchy = "observed_at"
    list_select_related = ("part",)


@admin.register(Group)
class GroupAdmin(ModelAdmin):
    list_display = ("name", "slug", "part_count")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Parts")
    def part_count(self, group):
        return group.parts.count()


@admin.register(Tag)
class TagAdmin(ModelAdmin):
    list_display = ("name", "slug", "part_count")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Parts")
    def part_count(self, tag):
        return tag.parts.count()
