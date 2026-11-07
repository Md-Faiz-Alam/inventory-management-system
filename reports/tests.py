"""Report permissions, filters, CSV serialization, and export boundaries."""

import csv
import io
import warnings
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.paginator import UnorderedObjectListWarning
from django.test import TestCase
from django.urls import reverse

from inventory.models import (
    MAX_STOCK_QUANTITY,
    Product,
    StockBalance,
    StockMovement,
)

from .csv_exports import spreadsheet_cell


class ReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()

        cls.admin = users.objects.create_superuser(
            username="report-admin",
            email="reports@example.com",
            password="Report-test-password-482!",
        )
        cls.member = users.objects.create_user(
            username="report-member"
        )
        cls.viewer = users.objects.create_user(
            username="report-viewer"
        )
        cls.movement_only = users.objects.create_user(
            username="movement-only"
        )

        cls.viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                codename="view_product",
            )
        )
        cls.movement_only.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                codename="view_stockmovement",
            )
        )

        cls.healthy = Product.objects.create(
            name="Healthy Report Product",
            sku="REPORT-HEALTHY",
            category="Accessories",
            reorder_level=5,
            unit_price=Decimal("19.90"),
        )
        cls.low = Product.objects.create(
            name="Low Report Product",
            sku="REPORT-LOW",
            reorder_level=5,
        )
        cls.empty = Product.objects.create(
            name="Empty Report Product",
            sku="REPORT-EMPTY",
            reorder_level=5,
        )
        cls.archived = Product.objects.create(
            name="Archived Report Product",
            sku="REPORT-ARCHIVED",
            is_active=False,
        )

        cls.receipt = StockMovement.objects.create(
            product=cls.healthy,
            actor=cls.admin,
            kind="receive",
            quantity=10,
            reason="Report opening delivery",
            operation_id=uuid4(),
            balance_before=0,
            balance_after=10,
        )
        cls.issue = StockMovement.objects.create(
            product=cls.healthy,
            actor=cls.admin,
            kind="issue",
            quantity=3,
            reason="Report customer order",
            operation_id=uuid4(),
            balance_before=10,
            balance_after=7,
        )

        StockBalance.objects.create(
            product=cls.healthy,
            quantity=7,
        )
        StockBalance.objects.create(
            product=cls.low,
            quantity=3,
        )

        StockMovement.objects.filter(
            pk=cls.receipt.pk
        ).update(
            created_at=datetime(
                2026, 11, 5, 18, 30, tzinfo=UTC
            )
        )
        StockMovement.objects.filter(
            pk=cls.issue.pk
        ).update(
            created_at=datetime(
                2026, 11, 6, 18, 29, 59, tzinfo=UTC
            )
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def csv_rows(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "text/csv; charset=utf-8",
        )
        self.assertIn(
            "attachment;",
            response["Content-Disposition"],
        )
        return list(
            csv.reader(
                io.StringIO(
                    response.content.decode("utf-8-sig")
                )
            )
        )

    def test_anonymous_reports_and_exports_require_login(self):
        self.client.logout()

        for name in ["report-stock", "report-movements"]:
            for parameters in [{}, {"format": "csv"}]:
                with self.subTest(
                    report=name,
                    parameters=parameters,
                ):
                    response = self.client.get(
                        reverse(name),
                        parameters,
                    )
                    self.assertEqual(
                        response.status_code,
                        302,
                    )
                    self.assertTrue(
                        response["Location"].startswith(
                            reverse("login")
                        )
                    )

    def test_member_cannot_read_or_export_reports(self):
        self.client.force_login(self.member)

        for name in ["report-stock", "report-movements"]:
            for parameters in [{}, {"format": "csv"}]:
                with self.subTest(
                    report=name,
                    parameters=parameters,
                ):
                    response = self.client.get(
                        reverse(name),
                        parameters,
                    )
                    self.assertEqual(
                        response.status_code,
                        403,
                    )

    def test_catalog_viewer_can_read_and_export_stock_only(self):
        self.client.force_login(self.viewer)

        for parameters in [{}, {"format": "csv"}]:
            stock = self.client.get(
                reverse("report-stock"),
                parameters,
            )
            movements = self.client.get(
                reverse("report-movements"),
                parameters,
            )
            self.assertEqual(stock.status_code, 200)
            self.assertEqual(movements.status_code, 403)

    def test_movement_permission_alone_does_not_grant_report_access(self):
        self.client.force_login(self.movement_only)

        for name in ["report-stock", "report-movements"]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 403)

    def test_staff_flag_does_not_grant_report_access(self):
        self.member.is_staff = True
        self.member.save(update_fields=["is_staff"])
        self.client.force_login(self.member)

        response = self.client.get(
            reverse("report-stock")
        )
        self.assertEqual(response.status_code, 403)

    def test_stock_report_totals_and_missing_balance(self):
        response = self.client.get(
            reverse("report-stock")
        )
        self.assertEqual(
            response.context["summary"],
            {
                "Products": 3,
                "Units on hand": 10,
                "Low stock": 1,
                "Out of stock": 1,
            },
        )

        rows = {
            row[0]: row
            for row in response.context["rows"]
        }
        self.assertEqual(
            rows[self.empty.sku][3],
            0,
        )
        self.assertEqual(
            rows[self.empty.sku][5],
            "Out of stock",
        )
        self.assertNotIn(self.archived.sku, rows)

    def test_stock_health_filters_match_html_and_csv(self):
        for health, product in [
            ("healthy", self.healthy),
            ("low", self.low),
            ("out", self.empty),
        ]:
            with self.subTest(health=health):
                response = self.client.get(
                    reverse("report-stock"),
                    {"health": health},
                )
                self.assertEqual(
                    [
                        row[0]
                        for row in response.context["rows"]
                    ],
                    [product.sku],
                )

                exported = self.csv_rows(
                    self.client.get(
                        reverse("report-stock"),
                        {
                            "health": health,
                            "format": "csv",
                        },
                    )
                )
                self.assertEqual(
                    [row[0] for row in exported[1:]],
                    [product.sku],
                )

    def test_low_stock_includes_reorder_threshold(self):
        StockBalance.objects.filter(
            product=self.low
        ).update(quantity=5)

        response = self.client.get(
            reverse("report-stock"),
            {"health": "low"},
        )
        self.assertEqual(
            [
                row[0]
                for row in response.context["rows"]
            ],
            [self.low.sku],
        )

    def test_stock_search_and_health_are_combined(self):
        response = self.client.get(
            reverse("report-stock"),
            {
                "q": "Accessories",
                "health": "low",
            },
        )
        self.assertEqual(
            response.context["page_obj"].paginator.count,
            0,
        )

        response = self.client.get(
            reverse("report-stock"),
            {"q": "Accessories"},
        )
        self.assertEqual(
            [
                row[0]
                for row in response.context["rows"]
            ],
            [self.healthy.sku],
        )

    def test_invalid_filters_reject_exports_without_attachment(self):
        cases = [
            ("report-stock", {"health": "invalid"}),
            ("report-stock", {"q": "x" * 101}),
            ("report-movements", {"kind": "invalid"}),
            ("report-movements", {"start": "bad-date"}),
            (
                "report-movements",
                {
                    "start": "2026-11-07",
                    "end": "2026-11-06",
                },
            ),
        ]

        for name, parameters in cases:
            for output in ["html", "csv"]:
                with self.subTest(
                    report=name,
                    parameters=parameters,
                    output=output,
                ):
                    response = self.client.get(
                        reverse(name),
                        {
                            **parameters,
                            "format": output,
                        },
                    )
                    self.assertEqual(
                        response.status_code,
                        400,
                    )
                    self.assertNotIn(
                        "Content-Disposition",
                        response.headers,
                    )
                    self.assertFalse(
                        response.context["rows"]
                    )
                    self.assertTrue(
                        response.context[
                            "filter_form"
                        ].errors
                    )

    def test_unknown_output_format_is_rejected(self):
        response = self.client.get(
            reverse("report-stock"),
            {"format": "xlsx"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Unsupported report format.",
            status_code=400,
        )

    def test_stock_csv_preserves_prices_and_large_integer_text(self):
        StockBalance.objects.filter(
            product=self.healthy
        ).update(quantity=MAX_STOCK_QUANTITY)

        response = self.client.get(
            reverse("report-stock"),
            {
                "q": self.healthy.sku,
                "format": "csv",
            },
        )
        rows = self.csv_rows(response)

        self.assertEqual(
            rows[1][3],
            str(MAX_STOCK_QUANTITY),
        )
        self.assertEqual(rows[1][6], "19.90")

    def test_csv_quotes_unicode_commas_quotes_and_newlines(self):
        name = 'Café, "special"\nproduct'
        Product.objects.filter(
            pk=self.healthy.pk
        ).update(name=name)

        rows = self.csv_rows(
            self.client.get(
                reverse("report-stock"),
                {
                    "q": self.healthy.sku,
                    "format": "csv",
                },
            )
        )
        self.assertEqual(rows[1][1], name)
        self.assertEqual(
            len(rows[0]),
            len(rows[1]),
        )

    def test_formula_like_text_is_prefixed_but_numbers_are_not(self):
        for value in [
            "=1+1",
            "+SUM(A1)",
            "-text",
            "@SUM(A1)",
            "  =1",
            "\ttext",
            "\rtext",
            "\ntext",
            "＝1",
            "＋1",
            "－1",
            "＠1",
        ]:
            with self.subTest(value=value):
                self.assertEqual(
                    spreadsheet_cell(value),
                    "'" + value,
                )

        self.assertEqual(
            spreadsheet_cell(-3),
            -3,
        )
        self.assertEqual(
            spreadsheet_cell("Normal text"),
            "Normal text",
        )

    def test_csv_protects_product_actor_and_reason_text(self):
        Product.objects.filter(
            pk=self.healthy.pk
        ).update(name="=1+1")

        get_user_model().objects.filter(
            pk=self.admin.pk
        ).update(username="@actor")

        StockMovement.objects.filter(
            pk=self.issue.pk
        ).update(reason="+formula")

        rows = self.csv_rows(
            self.client.get(
                reverse("report-movements"),
                {
                    "kind": "issue",
                    "format": "csv",
                },
            )
        )

        self.assertEqual(rows[1][2], "'=1+1")
        self.assertEqual(rows[1][4], "-3")
        self.assertEqual(rows[1][7], "'@actor")
        self.assertEqual(rows[1][8], "'+formula")

    def test_movement_totals_and_filtering(self):
        response = self.client.get(
            reverse("report-movements")
        )
        self.assertEqual(
            response.context["summary"],
            {
                "Movements": 2,
                "Units received": 10,
                "Units issued": 3,
                "Net units": 7,
            },
        )

        response = self.client.get(
            reverse("report-movements"),
            {
                "kind": "issue",
                "q": "customer",
            },
        )
        self.assertEqual(
            len(response.context["rows"]),
            1,
        )
        self.assertEqual(
            response.context["rows"][0][4],
            -3,
        )
        self.assertEqual(
            response.context["summary"]["Net units"],
            -3,
        )

    def test_date_boundaries_use_kolkata_and_are_inclusive(self):
        timestamps = [
            datetime(
                2026, 11, 5, 18, 29, 59, tzinfo=UTC
            ),
            datetime(
                2026, 11, 6, 18, 30, tzinfo=UTC
            ),
        ]

        for number, timestamp in enumerate(timestamps):
            movement = StockMovement.objects.create(
                product=self.healthy,
                actor=self.admin,
                kind="receive",
                quantity=1,
                reason="Outside date range",
                operation_id=uuid4(),
                balance_before=7 + number,
                balance_after=8 + number,
            )
            StockMovement.objects.filter(
                pk=movement.pk
            ).update(created_at=timestamp)

        parameters = {
            "start": "2026-11-06",
            "end": "2026-11-06",
        }

        response = self.client.get(
            reverse("report-movements"),
            parameters,
        )
        self.assertEqual(
            response.context["summary"]["Movements"],
            2,
        )

        exported = self.csv_rows(
            self.client.get(
                reverse("report-movements"),
                {
                    **parameters,
                    "format": "csv",
                },
            )
        )

        self.assertEqual(len(exported), 3)
        self.assertEqual(
            exported[1][0],
            "2026-11-06T23:59:59+05:30",
        )
        self.assertEqual(
            exported[2][0],
            "2026-11-06T00:00:00+05:30",
        )

    def test_open_ended_date_filters(self):
        response = self.client.get(
            reverse("report-movements"),
            {"start": "2026-11-07"},
        )
        self.assertEqual(
            response.context["page_obj"].paginator.count,
            0,
        )

        response = self.client.get(
            reverse("report-movements"),
            {"end": "2026-11-05"},
        )
        self.assertEqual(
            response.context["page_obj"].paginator.count,
            0,
        )

    def test_archiving_product_preserves_movement_report(self):
        Product.objects.filter(
            pk=self.healthy.pk
        ).update(is_active=False)

        rows = self.csv_rows(
            self.client.get(
                reverse("report-movements"),
                {"format": "csv"},
            )
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(
            rows[1][1],
            self.healthy.sku,
        )

    def test_export_includes_all_pages_in_stable_order(self):
        for number in range(26):
            Product.objects.create(
                name="Repeated Name",
                sku=f"PAGE-{number:02d}",
            )

        expected = list(
            Product.objects.filter(
                name="Repeated Name"
            )
            .order_by("name", "pk")
            .values_list("sku", flat=True)
        )

        with warnings.catch_warnings():
            warnings.simplefilter(
                "error",
                UnorderedObjectListWarning,
            )
            first = self.client.get(
                reverse("report-stock"),
                {"q": "Repeated Name"},
            )
            second = self.client.get(
                reverse("report-stock"),
                {
                    "q": "Repeated Name",
                    "page": "2",
                },
            )

        self.assertEqual(
            len(first.context["rows"]),
            25,
        )
        self.assertEqual(
            len(second.context["rows"]),
            1,
        )

        combined_rows = (
            first.context["rows"]
            + second.context["rows"]
        )
        self.assertEqual(
            [row[0] for row in combined_rows],
            expected,
        )
        self.assertEqual(
            second.context["summary"]["Products"],
            26,
        )

        rows = self.csv_rows(
            self.client.get(
                reverse("report-stock"),
                {
                    "q": "Repeated Name",
                    "page": "2",
                    "format": "csv",
                },
            )
        )
        self.assertEqual(
            [row[0] for row in rows[1:]],
            expected,
        )

    def test_export_limit_rejects_instead_of_truncating(self):
        cases = [
            ("report-stock", 2),
            ("report-movements", 1),
        ]

        for name, limit in cases:
            with self.subTest(report=name):
                with patch(
                    "reports.views.MAX_EXPORT_ROWS",
                    limit,
                ):
                    response = self.client.get(
                        reverse(name),
                        {"format": "csv"},
                    )

                self.assertEqual(
                    response.status_code,
                    400,
                )
                self.assertNotIn(
                    "Content-Disposition",
                    response.headers,
                )
                self.assertContains(
                    response,
                    "No file was exported.",
                    status_code=400,
                )

    def test_export_at_limit_succeeds(self):
        with patch(
            "reports.views.MAX_EXPORT_ROWS",
            3,
        ):
            rows = self.csv_rows(
                self.client.get(
                    reverse("report-stock"),
                    {"format": "csv"},
                )
            )

        self.assertEqual(len(rows), 4)

    def test_empty_export_contains_headers(self):
        rows = self.csv_rows(
            self.client.get(
                reverse("report-stock"),
                {
                    "q": "NO-SUCH-PRODUCT",
                    "format": "csv",
                },
            )
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "SKU")

    def test_reports_and_exports_are_not_cached(self):
        for name in ["report-stock", "report-movements"]:
            for parameters in [{}, {"format": "csv"}]:
                response = self.client.get(
                    reverse(name),
                    parameters,
                )
                self.assertIn(
                    "no-store",
                    response["Cache-Control"],
                )

    def test_reports_reject_post_and_do_not_modify_inventory(self):
        before = (
            Product.objects.count(),
            StockBalance.objects.count(),
            StockMovement.objects.count(),
        )

        for name in ["report-stock", "report-movements"]:
            response = self.client.post(reverse(name))
            self.assertEqual(response.status_code, 405)

            self.client.get(reverse(name))
            self.client.get(
                reverse(name),
                {"format": "csv"},
            )

        after = (
            Product.objects.count(),
            StockBalance.objects.count(),
            StockMovement.objects.count(),
        )
        self.assertEqual(before, after)
        self.assertEqual(
            StockBalance.objects.get(
                product=self.healthy
            ).quantity,
            7,
        )

    def test_report_navigation_requires_catalog_permission(self):
        for user, expected in [
            (self.viewer, True),
            (self.member, False),
        ]:
            self.client.force_login(user)

            response = self.client.get(
                reverse("overview")
            )
            link = (
                f'href="{reverse("report-stock")}"'
            )

            if expected:
                self.assertContains(response, link)
            else:
                self.assertNotContains(response, link)

    def test_report_html_escapes_text(self):
        Product.objects.filter(
            pk=self.healthy.pk
        ).update(
            name="<script>alert(1)</script>"
        )

        response = self.client.get(
            reverse("report-stock")
        )
        self.assertNotContains(
            response,
            "<script>alert(1)</script>",
        )
        self.assertContains(
            response,
            "&lt;script&gt;alert(1)&lt;/script&gt;",
        )