"""Read-only activity log."""

from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.views.decorators.cache import never_cache

from config.htmx import render_list
from inventory.views import pagination_query, workspace_context

from .models import AuditEntry


@never_cache
@login_required(login_url="login")
@permission_required("audit.view_auditentry", raise_exception=True)
def activity_list(request):
    query = request.GET.get("q", "").strip()[:100]
    action = request.GET.get("action", "")
    if action not in AuditEntry.Action.values:
        action = ""

    entries = AuditEntry.objects.select_related("content_type")
    if query:
        entries = entries.filter(
            Q(actor_username__icontains=query)
            | Q(message__icontains=query)
            | Q(object_repr__icontains=query)
        )
    if action:
        entries = entries.filter(action=action)

    context = workspace_context(request)
    context.update(
        {
            "active_page": "activity",
            "page_obj": Paginator(entries, 25).get_page(request.GET.get("page")),
            "query": query,
            "selected_action": action,
            "actions": AuditEntry.Action.choices,
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request, "audit/activity_list.html", "audit/_activity_rows.html", context
    )
