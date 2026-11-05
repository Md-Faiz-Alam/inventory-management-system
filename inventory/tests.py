"""Tests for workspace access and authentication."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="warehouse-user",
            email="warehouse@example.com",
            password="Workspace-test-password-482!",
        )

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(reverse("overview"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next=/",
        )

    def test_login_page_renders(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back.")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_authenticated_user_sees_account_details(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "warehouse@example.com")
        self.assertContains(response, "Your inventory starts here.")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_member_does_not_see_administration_link(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("overview"))

        self.assertNotContains(
            response,
            f'href="{reverse("admin:index")}"',
        )

    def test_staff_user_sees_administration_link(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("overview"))

        self.assertContains(
            response,
            f'href="{reverse("admin:index")}"',
        )

    def test_valid_login_redirects_to_workspace(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "Workspace-test-password-482!",
            },
        )

        self.assertRedirects(response, reverse("overview"))

    def test_login_rejects_external_redirect(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "Workspace-test-password-482!",
                "next": "https://example.com/untrusted",
            },
        )

        self.assertRedirects(response, reverse("overview"))

    def test_invalid_password_does_not_authenticate(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "incorrect-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign-in failed.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_get_request_cannot_log_user_out(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(reverse("logout"))

        self.assertEqual(response.status_code, 403)
        self.assertIn("_auth_user_id", client.session)

    def test_logout_with_csrf_token_ends_session(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(reverse("overview"))

        response = client.post(
            reverse("logout"),
            {"csrfmiddlewaretoken": client.cookies["csrftoken"].value},
        )

        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", client.session)