import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

log = logging.getLogger(__name__)


def notify(report):
    """Email every stock-report recipient. A failed email never loses the report itself."""
    recipients = settings.STOCK_REPORT_EMAIL
    if not recipients:
        return
    verb = "is out of stock" if report.kind == "out" else "is running low"
    subject = f"Stock report: {report.part.part_number} {report.part.short_name} {verb}"
    body = (
        f"{report.reported_by} reported that {report.part.part_number} "
        f"({report.part.short_name}) {verb}.\n\n"
        f"Reported at {timezone.localtime(report.reported_at):%Y-%m-%d %H:%M %Z}.\n"
        "Resolve it in the inventory admin under Stock reports."
    )
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients)
    except Exception:
        log.exception("Stock report email failed for report %s", report.pk)
