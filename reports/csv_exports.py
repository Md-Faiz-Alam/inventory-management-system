"""CSV serialization with quoting and text formula-prefix handling."""

import csv

from django.http import HttpResponse


def spreadsheet_cell(value):
    """Prefix risky text; keep actual numeric values numeric."""
    if not isinstance(value, str):
        return value

    stripped = value.lstrip()

    if value.startswith(("\t", "\r", "\n")) or stripped.startswith(
        ("=", "+", "-", "@", "＝", "＋", "－", "＠")
    ):
        return "'" + value

    return value


def csv_response(filename, columns, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    response["X-Content-Type-Options"] = "nosniff"
    response.write("\ufeff")

    writer = csv.writer(response, quoting=csv.QUOTE_ALL)
    writer.writerow(columns)

    for row in rows:
        writer.writerow(
            [spreadsheet_cell(value) for value in row]
        )

    return response