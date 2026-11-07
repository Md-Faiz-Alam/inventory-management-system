"""Small helpers for HTMX partial responses."""

import json

from django.shortcuts import render


def is_htmx(request):
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Boosted") != "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )


def render_list(request, template, partial, context, status=200):
    """Render only the results partial for HTMX requests, else the full page."""
    response = render(
        request, partial if is_htmx(request) else template, context, status=status
    )
    if is_htmx(request):
        response["Vary"] = "HX-Request"
    return response


def toast(response, message, level="success"):
    """Ask the page to show a toast after an HTMX swap."""
    response["HX-Trigger"] = json.dumps({"toast": {"message": message, "level": level}})
    return response
