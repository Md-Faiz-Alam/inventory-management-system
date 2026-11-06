"""Read a report page and its totals in one database statement."""

from django.core.paginator import Page, Paginator
from django.db.models import BigIntegerField, Count, ExpressionWrapper, F, Value, Window
from django.db.models.functions import Least, RowNumber

MAX_PAGE_NUMBER = 9_223_372_036_854775807


def requested_page(value):
    """Preserve get_page semantics while bounding database parameters."""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 1

    return min(number, MAX_PAGE_NUMBER) if number > 0 else MAX_PAGE_NUMBER


def snapshot_page(records, *, summary_fields, order_by, page_number, page_size):
    """Return bounded rows, count, and totals from the same SQL snapshot."""
    if page_size < 1:
        raise ValueError("Page size must be positive.")

    aliases = {
        label: f"_report_summary_{index}"
        for index, label in enumerate(summary_fields)
    }

    annotated = records.annotate(
        _report_total=Window(Count("*")),
        _report_position=Window(RowNumber(), order_by=list(order_by)),
        **{
            aliases[label]: Window(expression)
            for label, expression in summary_fields.items()
        },
    )

    last_page = ExpressionWrapper(
        (F("_report_total") + Value(page_size - 1)) / Value(page_size),
        output_field=BigIntegerField(),
    )

    annotated = annotated.annotate(
        _report_page=Least(
            Value(requested_page(page_number), output_field=BigIntegerField()),
            last_page,
        )
    )

    lower = (F("_report_page") - Value(1)) * Value(page_size)
    upper = F("_report_page") * Value(page_size)

    rows = list(
        annotated.filter(
            _report_position__gt=lower,
            _report_position__lte=upper,
        ).order_by(*order_by)
    )

    total = int(rows[0]["_report_total"]) if rows else 0
    number = int(rows[0]["_report_page"]) if rows else 1

    summary = {
        label: (rows[0][alias] or 0) if rows else 0
        for label, alias in aliases.items()
    }

    # Pagination metadata uses an allocation-free range, not another SQL count.
    paginator = Paginator(range(total), page_size)
    page = Page(rows, number, paginator)

    return page, summary