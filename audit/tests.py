"""Audit trail and dashboard endpoints."""

from django.test import TestCase
from django.urls import reverse

from accounts.roles import MANAGER, STAFF
from config.editing import edit_token
from config.testing import make_product, make_user, receive

from .models import AuditEntry


class AuditTrailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = make_user("auditor", MANAGER)
        cls.staff = make_user("worker", STAFF)
        cls.product = make_product("AUD-1", "Audited", "10.00")

    def test_product_edit_records_field_changes(self):
        self.client.force_login(self.manager)
        self.client.post(
            reverse("product-update", args=[self.product.pk]),
            {
                "name": "Audited",
                "sku": "AUD-1",
                "category": "",
                "description": "",
                "unit_price": "12.50",
                "reorder_level": "5",
                "edit_token": edit_token(self.product),
            },
        )
        entry = AuditEntry.objects.get(action="update")
        self.assertEqual(entry.changes, {"unit_price": ["10.00", "12.50"]})
        self.assertEqual(entry.actor, self.manager)

    def test_activity_page_is_manager_only(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("activity")).status_code, 403)
        self.client.force_login(self.manager)
        response = self.client.get(reverse("activity"), {"action": "login"})
        self.assertEqual(response.status_code, 200)


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = make_user("dash", MANAGER)
        low = make_product("LOW-1", "Low", "100.00", reorder=5)
        healthy = make_product("OK-1", "Okay", "10.00", reorder=1, category="Tools")
        receive(low, cls.manager, 2)
        receive(healthy, cls.manager, 30)

    def test_dashboard_shows_stock_value(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("overview"))
        self.assertEqual(str(response.context["stock_value"]), "500.00")
        self.assertEqual(response.context["open_purchase_orders"], 0)

    def test_chart_data(self):
        self.client.force_login(self.manager)
        data = self.client.get(reverse("dashboard-data")).json()
        self.assertEqual(data["health"]["values"], [1, 1, 0])
        self.assertEqual(sum(data["movements"]["received"]), 32)
        self.assertEqual(len(data["movements"]["labels"]), 30)
        self.assertIn("Tools", data["value_by_category"]["labels"])

    def test_alert_bell_counts_low_stock(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("alerts"))
        self.assertContains(response, 'class="alert-count">1<')

    def test_member_without_catalog_gets_no_chart_data(self):
        self.client.force_login(make_user("nobody"))
        self.assertEqual(self.client.get(reverse("dashboard-data")).json(), {})
