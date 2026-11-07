"""Role presets stored as Django groups. Kept in sync after every migrate."""

from django.contrib.auth.models import Group, Permission

ADMIN, MANAGER, STAFF, VIEWER = "Admin", "Manager", "Staff", "Viewer"
ROLE_ORDER = [ADMIN, MANAGER, STAFF, VIEWER]

ROLE_DESCRIPTIONS = {
    ADMIN: "Everything, including users, roles and the activity log.",
    MANAGER: "Catalog, suppliers, customers, purchasing, sales, corrections, activity log.",
    STAFF: "View catalog, receive and issue stock, receive deliveries, fulfil sales.",
    VIEWER: "Read-only: can open every operational page but cannot change anything.",
}

MANAGER_PERMISSIONS = [
    "inventory.view_product",
    "inventory.add_product",
    "inventory.change_product",
    "inventory.view_stockbalance",
    "inventory.view_stockmovement",
    "inventory.receive_stock",
    "inventory.issue_stock",
    "inventory.correct_stock",
    "inventory.view_purchaseorder",
    "inventory.add_purchaseorder",
    "inventory.change_purchaseorder",
    "suppliers.view_supplier",
    "suppliers.add_supplier",
    "suppliers.change_supplier",
    "sales.view_customer",
    "sales.add_customer",
    "sales.change_customer",
    "sales.view_salesorder",
    "sales.add_salesorder",
    "sales.change_salesorder",
    "sales.fulfil_salesorder",
    "audit.view_auditentry",
]

STAFF_PERMISSIONS = [
    "inventory.view_product",
    "inventory.view_stockbalance",
    "inventory.view_stockmovement",
    "inventory.receive_stock",
    "inventory.issue_stock",
    "inventory.view_purchaseorder",
    "inventory.change_purchaseorder",
    "suppliers.view_supplier",
    "sales.view_customer",
    "sales.view_salesorder",
    "sales.add_salesorder",
    "sales.fulfil_salesorder",
]

# Read-only. The activity log is left out because it shows people's IP addresses.
VIEWER_PERMISSIONS = [
    "inventory.view_product",
    "inventory.view_stockbalance",
    "inventory.view_stockmovement",
    "inventory.view_purchaseorder",
    "suppliers.view_supplier",
    "sales.view_customer",
    "sales.view_salesorder",
]

ADMIN_PERMISSIONS = [
    *MANAGER_PERMISSIONS,
    "accounts.view_user",
    "accounts.add_user",
    "accounts.change_user",
]

ROLE_PERMISSIONS = {
    ADMIN: ADMIN_PERMISSIONS,
    MANAGER: MANAGER_PERMISSIONS,
    STAFF: STAFF_PERMISSIONS,
    VIEWER: VIEWER_PERMISSIONS,
}


def resolve(codes):
    perms = []
    for code in codes:
        app_label, codename = code.split(".", 1)
        perm = Permission.objects.filter(
            content_type__app_label=app_label, codename=codename
        ).first()
        if perm is not None:
            perms.append(perm)
    return perms


def sync_roles():
    """Create role groups and set their permissions exactly."""
    for name, codes in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(resolve(codes))


GUEST_USERNAME = "demo_guest"
DEMO_USERNAMES = {"demo_admin", "demo_manager", "demo_staff", GUEST_USERNAME}


def is_protected_demo(user):
    """Public demo accounts cannot be locked out by visitors in demo mode."""
    from django.conf import settings

    if user is None or user.get_username() not in DEMO_USERNAMES:
        return False
    return bool(
        getattr(settings, "DEMO_MODE", False)
        or (user.get_username() == GUEST_USERNAME and getattr(settings, "GUEST_ACCESS", False))
    )


def guest_account():
    """The read-only guest account, if guest access is on and the account is safe."""
    from django.conf import settings
    from django.contrib.auth import get_user_model

    if not getattr(settings, "GUEST_ACCESS", False):
        return None
    guest = (
        get_user_model()
        .objects.filter(username=GUEST_USERNAME, is_active=True, is_superuser=False, is_staff=False)
        .first()
    )
    if guest is None or role_of(guest) != VIEWER:
        return None
    return guest


def is_guest(user):
    return getattr(user, "is_authenticated", False) and user.get_username() == GUEST_USERNAME


def role_of(user):
    if not getattr(user, "is_authenticated", False):
        return ""
    if user.is_superuser:
        return "Administrator"
    # .all() uses prefetch_related("groups") when the caller loaded it.
    names = {group.name for group in user.groups.all()}
    for role in ROLE_ORDER:
        if role in names:
            return role
    return "Member"


def set_role(user, role):
    user.groups.remove(*Group.objects.filter(name__in=ROLE_ORDER))
    if role:
        user.groups.add(Group.objects.get(name=role))
