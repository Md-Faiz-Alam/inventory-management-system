"""Render Django templates to PDF with xhtml2pdf (pure Python, no system libs)."""

from io import BytesIO

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa


def company():
    return getattr(
        settings,
        "COMPANY",
        {"name": "StockFlow Demo Traders", "address": "", "gstin": "", "email": "", "phone": ""},
    )


def render_pdf(request, template, context, filename, inline=True):
    context = {**context, "company": company(), "generated_at": timezone.now()}
    html = render_to_string(template, context, request=request)
    buffer = BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if result.err:
        return HttpResponse("The PDF could not be generated.", status=500)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
