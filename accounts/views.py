"""Sign-in with rate limiting, profile, and user management."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from audit.log import diff, record
from audit.models import AuditEntry
from config.htmx import render_list
from inventory.views import pagination_query, workspace_context

from . import lockout
from .forms import ProfileForm, UserCreateForm, UserEditForm
from .models import User
from .roles import (
    ROLE_ORDER,
    guest_account,
    is_protected_demo,
    role_of,
    set_role,
)

class RateLimitedLoginView(auth_views.LoginView):
    """Lock a username+IP pair for a while after repeated failures."""

    template_name = "accounts/login.html"
    next_page = reverse_lazy("overview")
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["demo_accounts"] = getattr(settings, "DEMO_LOGINS", [])
        context["guest_access"] = guest_account() is not None
        return context

    def post(self, request, *args, **kwargs):
        if lockout.is_locked(request, request.POST.get("username")):
            context = self.get_context_data(form=self.get_form(), locked=True)
            return self.render_to_response(context, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        lockout.record_failure(self.request, self.request.POST.get("username"))
        return super().form_invalid(form)

    def form_valid(self, form):
        lockout.clear(self.request, self.request.POST.get("username"))
        return super().form_valid(form)


@require_POST
def guest_login(request):
    """Sign a visitor in as the shared read-only guest, no password needed."""
    guest = guest_account()
    if guest is None:
        # Refuse unless the account is exactly the read-only guest.
        messages.error(request, "Guest access is not available right now.")
        return redirect("login")
    login(request, guest, backend="django.contrib.auth.backends.ModelBackend")
    request.session.set_expiry(settings.GUEST_SESSION_SECONDS)
    messages.info(request, "You are exploring as a guest. Everything is read-only.")
    return redirect("overview")


@never_cache
@login_required(login_url="login")
@require_http_methods(["GET", "POST"])
def profile(request):
    user = request.user
    profile_form = ProfileForm(
        request.POST if request.POST.get("form") == "profile" else None, instance=user
    )
    password_form = PasswordChangeForm(
        user, request.POST if request.POST.get("form") == "password" else None
    )

    if request.method == "POST" and is_protected_demo(user):
        messages.error(request, "Demo accounts are shared, so their details cannot be changed.")
        return redirect("profile")

    if request.method == "POST":
        if request.POST.get("form") == "profile" and profile_form.is_valid():
            profile_form.save()
            record(request, AuditEntry.Action.UPDATE, "Updated own profile", obj=user)
            messages.success(request, "Your profile was updated.")
            return redirect("profile")
        if request.POST.get("form") == "password" and password_form.is_valid():
            password_form.save()
            update_session_auth_hash(request, password_form.user)
            record(request, AuditEntry.Action.SECURITY, "Changed own password", obj=user)
            messages.success(request, "Your password was changed.")
            return redirect("profile")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "profile",
            "profile_form": profile_form,
            "password_form": password_form,
            "role": role_of(user),
            "permissions": sorted(user.get_all_permissions()),
            "demo_locked": is_protected_demo(user),
        }
    )
    return render(request, "accounts/profile.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(["accounts.view_user", "accounts.change_user"], raise_exception=True)
def user_list(request):
    query = request.GET.get("q", "").strip()[:100]
    users = User.objects.prefetch_related("groups").order_by("username")
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        )
    page = Paginator(users, 15).get_page(request.GET.get("page"))
    for user in page:
        user.role = role_of(user)
        user.is_demo = is_protected_demo(user)
        user.locked = user.is_demo and not request.user.is_superuser
    context = workspace_context(request)
    context.update(
        {
            "active_page": "users",
            "page_obj": page,
            "query": query,
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request, "accounts/user_list.html", "accounts/_user_rows.html", context
    )


@never_cache
@login_required(login_url="login")
@permission_required(
    ["accounts.view_user", "accounts.add_user", "accounts.change_user"],
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def user_create(request):
    form = UserCreateForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            set_role(user, form.cleaned_data["role"])
        record(
            request,
            AuditEntry.Action.CREATE,
            f"Created user {user.username} as {form.cleaned_data['role']}",
            obj=user,
        )
        messages.success(request, f"User {user.username} was created.")
        return redirect("user-list")

    context = workspace_context(request)
    context.update(
        {"active_page": "users", "form": form, "page_title": "Add user", "submit_label": "Create user"}
    )
    return render(request, "accounts/user_form.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(["accounts.view_user", "accounts.change_user"], raise_exception=True)
@require_http_methods(["GET", "POST"])
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    current_role = role_of(target)
    refusal = edit_refusal(request, target)
    if refusal:
        messages.error(request, refusal)
        return redirect("user-list")

    form = UserEditForm(
        request.POST if request.method == "POST" else None,
        instance=target,
        initial={"role": current_role if current_role in ROLE_ORDER else "Staff"},
    )
    before = {"role": current_role, "is_active": target.is_active}

    if request.method == "POST" and form.is_valid():
        if target == request.user and (
            not form.cleaned_data["is_active"]
            or (current_role == "Admin" and form.cleaned_data["role"] != "Admin")
        ):
            form.add_error(None, "You cannot deactivate yourself or remove your own Admin role.")
        else:
            with transaction.atomic():
                form.save()
                set_role(target, form.cleaned_data["role"])
            after = {"role": form.cleaned_data["role"], "is_active": target.is_active}
            record(
                request,
                AuditEntry.Action.UPDATE,
                f"Updated user {target.username}",
                obj=target,
                changes=diff(before, after),
            )
            messages.success(request, f"User {target.username} was updated.")
            return redirect("user-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "users",
            "form": form,
            "target": target,
            "page_title": f"Edit {target.username}",
            "submit_label": "Save changes",
        }
    )
    return render(request, "accounts/user_form.html", context)


def edit_refusal(request, target):
    """Why the current user may not change this account, or "" if they may."""
    if target.is_superuser and not request.user.is_superuser:
        return "Only a superuser can edit a superuser account."
    if is_protected_demo(target) and not request.user.is_superuser:
        # Visitors share these accounts; only the site owner (a superuser) may edit them.
        return "Demo accounts are shared by visitors and cannot be changed."
    return ""


@never_cache
@login_required(login_url="login")
@permission_required(["accounts.view_user", "accounts.change_user"], raise_exception=True)
@require_http_methods(["GET", "POST"])
def user_password(request, pk):
    """Let an admin set a new password, since the app sends no reset emails."""
    target = get_object_or_404(User, pk=pk)
    refusal = edit_refusal(request, target)
    if refusal:
        messages.error(request, refusal)
        return redirect("user-list")
    if target == request.user:
        return redirect("profile")

    form = SetPasswordForm(target, request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        form.save()
        record(
            request,
            AuditEntry.Action.SECURITY,
            f"Set a new password for {target.username}",
            obj=target,
        )
        messages.success(request, f"A new password was set for {target.username}.")
        return redirect("user-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "users",
            "form": form,
            "target": target,
            "page_title": f"New password for {target.username}",
            "submit_label": "Set password",
        }
    )
    return render(request, "accounts/user_form.html", context)
