"""Roles, user management, profile and login rate limiting."""

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.roles import ADMIN, MANAGER, STAFF, ROLE_PERMISSIONS, role_of
from audit.models import AuditEntry
from config.testing import PASSWORD, make_user

from .models import User


class RoleTests(TestCase):
    def test_roles_exist_with_expected_permissions(self):
        for role in (ADMIN, MANAGER, STAFF):
            group = Group.objects.get(name=role)
            codes = {
                f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()
            }
            self.assertEqual(codes, set(ROLE_PERMISSIONS[role]))

    def test_role_label(self):
        self.assertEqual(role_of(make_user("m", MANAGER)), MANAGER)
        self.assertEqual(role_of(make_user("plain")), "Member")
        self.assertEqual(role_of(make_user("root", is_superuser=True)), "Administrator")

    def test_staff_cannot_add_products_but_can_issue(self):
        staff = make_user("floor", STAFF)
        self.assertFalse(staff.has_perm("inventory.add_product"))
        self.assertTrue(staff.has_perm("inventory.issue_stock"))
        self.assertTrue(staff.has_perm("sales.fulfil_salesorder"))
        self.assertFalse(staff.has_perm("sales.change_salesorder"))


class UserManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("boss", ADMIN)
        cls.manager = make_user("ops", MANAGER)

    def test_only_admin_role_reaches_user_pages(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("user-list")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("user-list")).status_code, 200)

    def test_admin_creates_user_with_role(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("user-create"),
            {
                "username": "newbie",
                "first_name": "New",
                "last_name": "Person",
                "email": "new@example.com",
                "role": STAFF,
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertRedirects(response, reverse("user-list"))
        self.assertEqual(role_of(User.objects.get(username="newbie")), STAFF)
        self.assertTrue(AuditEntry.objects.filter(message__contains="newbie").exists())

    def test_admin_changes_role_and_cannot_demote_self(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("user-edit", args=[self.manager.pk]),
            {"first_name": "", "last_name": "", "email": "", "is_active": "on", "role": STAFF},
        )
        self.assertRedirects(response, reverse("user-list"))
        self.assertEqual(role_of(User.objects.get(pk=self.manager.pk)), STAFF)

        response = self.client.post(
            reverse("user-edit", args=[self.admin.pk]),
            {"first_name": "", "last_name": "", "email": "", "role": MANAGER},
        )
        self.assertContains(response, "cannot deactivate yourself")
        self.assertEqual(role_of(User.objects.get(pk=self.admin.pk)), ADMIN)

    def test_profile_password_change(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("profile"),
            {
                "form": "password",
                "old_password": PASSWORD,
                "new_password1": "Another-strong-pass-913!",
                "new_password2": "Another-strong-pass-913!",
            },
        )
        self.assertRedirects(response, reverse("profile"))
        self.assertTrue(
            User.objects.get(pk=self.manager.pk).check_password("Another-strong-pass-913!")
        )


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("target")

    def test_lockout_after_repeated_failures(self):
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "target", "password": "wrong"})
        response = self.client.post(
            reverse("login"), {"username": "target", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many failed attempts", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(AuditEntry.objects.filter(action="login_failed").count(), 5)

    def test_successful_login_is_audited(self):
        self.client.post(reverse("login"), {"username": "target", "password": PASSWORD})
        self.assertTrue(AuditEntry.objects.filter(action="login", actor=self.user).exists())


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CLIENT_IP_HEADER="HTTP_TRUE_CLIENT_IP",
)
class ClientIpTests(TestCase):
    def setUp(self):
        cache.clear()
        make_user("victim")

    def test_spoofed_forwarded_for_does_not_reset_the_lockout(self):
        for attempt in range(5):
            self.client.post(
                reverse("login"),
                {"username": "victim", "password": "wrong"},
                HTTP_TRUE_CLIENT_IP="203.0.113.7",
                HTTP_X_FORWARDED_FOR=f"10.0.0.{attempt}",
            )
        response = self.client.post(
            reverse("login"),
            {"username": "victim", "password": PASSWORD},
            HTTP_TRUE_CLIENT_IP="203.0.113.7",
            HTTP_X_FORWARDED_FOR="10.9.9.9",
        )
        self.assertEqual(response.status_code, 429)
        entry = AuditEntry.objects.filter(action="login_failed").first()
        self.assertEqual(entry.ip_address, "203.0.113.7")


@override_settings(DEMO_MODE=True)
class DemoAccountProtectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("demo_admin", ADMIN)
        cls.staff = make_user("demo_staff", STAFF)
        cls.real = make_user("real_person", STAFF)

    def test_demo_user_cannot_change_own_password(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("profile"),
            {
                "form": "password",
                "old_password": PASSWORD,
                "new_password1": "Hijacked-password-913!",
                "new_password2": "Hijacked-password-913!",
            },
        )
        self.assertTrue(User.objects.get(pk=self.admin.pk).check_password(PASSWORD))

    def test_demo_users_cannot_be_edited_or_reset(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("user-edit", args=[self.staff.pk]),
            {"first_name": "", "last_name": "", "email": "", "role": STAFF},
        )
        self.assertTrue(User.objects.get(pk=self.staff.pk).is_active)
        response = self.client.get(reverse("user-password", args=[self.staff.pk]))
        self.assertRedirects(response, reverse("user-list"))

    def test_admin_can_set_password_for_real_users(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("user-password", args=[self.real.pk]),
            {"new_password1": "Fresh-password-5521!", "new_password2": "Fresh-password-5521!"},
        )
        self.assertRedirects(response, reverse("user-list"))
        self.assertTrue(User.objects.get(pk=self.real.pk).check_password("Fresh-password-5521!"))


@override_settings(GUEST_ACCESS=True, DEMO_MODE=False)
class GuestAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounts.roles import VIEWER
        from config.testing import make_product

        cls.guest = make_user("demo_guest", VIEWER)
        cls.guest.set_unusable_password()
        cls.guest.save()
        cls.product = make_product()

    def test_one_click_guest_login_is_read_only(self):
        response = self.client.post(reverse("guest-login"))
        self.assertRedirects(response, reverse("overview"))
        page = self.client.get(reverse("product-list"))
        self.assertContains(page, "Guest mode")
        self.assertNotContains(page, "+ Add product")
        for url in [reverse("product-create"), reverse("po-create"), reverse("customer-create"),
                    reverse("stock-receive", args=[self.product.pk]), reverse("activity"),
                    reverse("user-list")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(reverse("stock-list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("order-list")).status_code, 200)

    def test_guest_cannot_change_its_profile_or_use_api_writes(self):
        self.client.post(reverse("guest-login"))
        self.client.post(reverse("profile"), {"form": "profile", "first_name": "Hacker", "last_name": "", "email": ""})
        self.assertNotEqual(User.objects.get(pk=self.guest.pk).first_name, "Hacker")
        response = self.client.post(
            "/api/v1/products/", {"name": "X", "sku": "X-1", "unit_price": "1", "reorder_level": 1}
        )
        self.assertEqual(response.status_code, 403)

    def test_guest_has_no_password_login(self):
        response = self.client.post(reverse("login"), {"username": "demo_guest", "password": ""})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 200)

    def test_landing_shows_guest_button(self):
        self.assertContains(self.client.get(reverse("overview")), "Explore as guest")

    @override_settings(GUEST_ACCESS=False)
    def test_disabled_guest_access(self):
        self.client.post(reverse("guest-login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotContains(self.client.get(reverse("overview")), "Explore as guest")

    def test_refuses_when_guest_account_was_given_admin_rights(self):
        User.objects.filter(pk=self.guest.pk).update(is_superuser=True)
        self.client.post(reverse("guest-login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_get_request_does_not_sign_in(self):
        self.assertEqual(self.client.get(reverse("guest-login")).status_code, 405)


@override_settings(GUEST_ACCESS=True)
class GuestButtonVisibilityTests(TestCase):
    def test_button_hidden_until_guest_account_exists(self):
        self.assertNotContains(self.client.get(reverse("overview")), "Explore as guest")
        from accounts.roles import VIEWER

        make_user("demo_guest", VIEWER)
        self.assertContains(self.client.get(reverse("overview")), "Explore as guest")
        self.assertContains(self.client.get(reverse("login")), "Explore as guest")


@override_settings(DEMO_MODE=True)
class OwnerCanEditDemoAccountsTests(TestCase):
    def test_superuser_can_rename_demo_staff(self):
        owner = make_user("owner", is_superuser=True, is_staff=True)
        staff = make_user("demo_staff", STAFF)
        self.client.force_login(owner)
        listing = self.client.get(reverse("user-list"))
        self.assertContains(listing, reverse("user-edit", args=[staff.pk]))
        response = self.client.post(
            reverse("user-edit", args=[staff.pk]),
            {"first_name": "Ravi", "last_name": "Kumar", "email": "", "is_active": "on", "role": STAFF},
        )
        self.assertRedirects(response, reverse("user-list"))
        self.assertEqual(User.objects.get(pk=staff.pk).get_full_name(), "Ravi Kumar")
