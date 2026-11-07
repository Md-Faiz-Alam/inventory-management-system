"""Prepare a deployment: roles, optional superuser, demo users and demo data.

Safe to run on every deploy. Nothing is deleted; existing records are kept.
Passwords come only from environment variables.
"""

import os
from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.roles import (
    ADMIN,
    GUEST_USERNAME,
    MANAGER,
    STAFF,
    VIEWER,
    set_role,
    sync_roles,
)
from inventory.models import Product, PurchaseOrder, StockMovement
from inventory.services import record_stock_movement
from sales.models import Customer, SalesOrder, SalesOrderLine
from sales.services import confirm_order, fulfil_order
from suppliers.models import Supplier

DEMO_USERS = [
    ("demo_admin", ADMIN, "Aditi", "Admin"),
    ("demo_manager", MANAGER, "Rahul", "Manager"),
    ("demo_staff", STAFF, "Sana", "Staff"),
]

CUSTOMERS = [
    ("DEMO-CUST-01", "Sharma Electronics", "orders@sharma-electronics.example", "+91 98100 00001", "Lajpat Nagar, New Delhi"),
    ("DEMO-CUST-02", "Patel Office Supplies", "buy@patel-office.example", "+91 98200 00002", "CG Road, Ahmedabad"),
    ("DEMO-CUST-03", "Green Leaf Cafe", "manager@greenleaf.example", "+91 98300 00003", "Park Street, Kolkata"),
    ("DEMO-CUST-04", "Metro Hardware Mart", "accounts@metrohardware.example", "+91 98400 00004", "T Nagar, Chennai"),
]


class Command(BaseCommand):
    help = "Sync roles, create demo users from env passwords, and load demo data once."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-data",
            action="store_true",
            help="Only sync roles and accounts.",
        )

    def handle(self, *args, **options):
        sync_roles()
        self.stdout.write("Roles synchronised: Admin, Manager, Staff, Viewer.")
        owner = self.ensure_superuser()
        demo_users = self.ensure_demo_users()
        self.ensure_guest()

        # Demo data is recorded as the first demo account that may create it.
        # Using a demo account (not your own) keeps your admin history clean.
        actor = demo_users.get(ADMIN) or demo_users.get(MANAGER) or owner
        if options["skip_data"] or actor is None:
            if actor is None:
                if Customer.objects.filter(code="DEMO-CUST-01").exists():
                    self.stdout.write("Demo data already loaded. No changes.")
                else:
                    self.stdout.write(
                        "Demo data not loaded: set DJANGO_SUPERUSER_USERNAME and "
                        "DJANGO_SUPERUSER_PASSWORD (or a demo password) and run again."
                    )
            return

        if Product.objects.filter(sku__iexact="DEMO-V1-001").exists():
            # Someone already loaded the catalog (possibly as another user, e.g. with
            # `seed_demo --actor <you>`). Reuse it rather than refusing to continue.
            self.stdout.write("Demo catalog already present; keeping it as it is.")
        else:
            call_command("seed_demo", actor=actor.username, stdout=self.stdout)
        self.trade_data(actor)

    # ------------------------------------------------------------ accounts

    def ensure_superuser(self):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "").strip()
        if not username or not password:
            return None
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": os.environ.get("DJANGO_SUPERUSER_EMAIL", "")},
        )
        if created:
            # Created once. Later deploys never change it, so your own password,
            # profile and anything you set in the app stay exactly as you left them.
            user.is_staff = True
            user.is_superuser = True
            user.set_password(password)
            user.save()
            self.stdout.write(f"Created superuser {username}.")
        if not user.is_superuser:
            return None
        return User.objects.get(pk=user.pk)

    def ensure_demo_users(self):
        """Create or refresh demo users whose password is set; returns {role: user}."""
        User = get_user_model()
        created_users = {}
        for username, role, first, last in DEMO_USERS:
            password = os.environ.get(f"DEMO_{role.upper()}_PASSWORD", "").strip()
            if not password:
                # No password configured means this login is not offered. Switch off
                # any existing account so an old, once-published password stops working.
                switched_off = User.objects.filter(
                    username=username, is_active=True, is_superuser=False, is_staff=False
                ).update(is_active=False)
                if switched_off:
                    self.stdout.write(f"Deactivated {username}: no DEMO_{role.upper()}_PASSWORD set.")
                continue
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last, "email": f"{username}@example.com"},
            )
            if user.is_superuser or user.is_staff:
                self.stdout.write(f"Skipped {username}: it is an administrator account.")
                continue
            user.is_active = True
            user.set_password(password)
            user.save()
            set_role(user, role)
            self.stdout.write(f"{'Created' if created else 'Updated'} demo user {username} ({role}).")
            created_users[role] = User.objects.get(pk=user.pk)  # fresh permission cache
        return created_users

    def ensure_guest(self):
        """The read-only guest behind "Explore as guest". It has no usable password."""
        if not settings.GUEST_ACCESS:
            return
        User = get_user_model()
        guest, created = User.objects.get_or_create(
            username=GUEST_USERNAME,
            defaults={"first_name": "Guest", "email": "guest@example.com"},
        )
        if guest.is_superuser or guest.is_staff:
            self.stdout.write(f"Skipped {GUEST_USERNAME}: it is an administrator account.")
            return
        guest.is_active = True
        guest.set_unusable_password()
        guest.save()
        set_role(guest, VIEWER)
        self.stdout.write(f"{'Created' if created else 'Updated'} read-only guest {GUEST_USERNAME}.")

    # ---------------------------------------------------------------- data

    def trade_data(self, actor):
        if Customer.objects.filter(code="DEMO-CUST-01").exists():
            self.stdout.write("Demo customers and orders already exist. No changes.")
            return

        now = timezone.now()
        with transaction.atomic():
            customers = [
                Customer.objects.create(
                    code=code, name=name, email=email, phone=phone, address=address,
                    notes="Synthetic demonstration customer.",
                )
                for code, name, email, phone, address in CUSTOMERS
            ]

            # Products the seed leaves with healthy stock (20 units each).
            healthy = list(
                Product.objects.filter(sku__startswith="DEMO-V1-", is_active=True)
                .filter(stock_balance__quantity__gte=20)
                .order_by("sku")
            )
            low = list(
                Product.objects.filter(
                    sku__startswith="DEMO-V1-", is_active=True, stock_balance__quantity__lt=5
                ).order_by("sku")[:6]
            )
            suppliers = list(Supplier.objects.filter(code__startswith="DEMO-SUP-V1-", is_active=True))

            # Purchase orders: two received in full, two in part, two open.
            for index, product in enumerate(low):
                supplier = product.suppliers.filter(is_active=True).first() or suppliers[index % len(suppliers)]
                order = PurchaseOrder.objects.create(
                    reference=f"PO-DEMO-{index + 1:03d}",
                    supplier=supplier,
                    product=product,
                    quantity=30,
                    unit_cost=(product.unit_price * Decimal("0.62")).quantize(Decimal("0.01")),
                    actor=actor,
                )
                received = [30, 30, 12, 18, 0, 0][index]
                if received:
                    result = record_stock_movement(
                        product_id=product.pk,
                        actor=actor,
                        kind="receive",
                        quantity=received,
                        reason=f"Delivery against {order.reference}",
                        operation_id=uuid5(NAMESPACE_URL, f"stockflow-demo:po:{order.reference}"),
                        purchase_order_id=order.pk,
                        document_reference=f"DC-{4100 + index}",
                    )
                    StockMovement.objects.filter(pk=result.movement.pk).update(
                        created_at=now - timedelta(days=9 - index)
                    )
                PurchaseOrder.objects.filter(pk=order.pk).update(
                    created_at=now - timedelta(days=12 - index)
                )

            # Sales orders: four fulfilled, one confirmed, one draft.
            plan = [
                (0, [(0, 4), (1, 3)], "fulfil", 14),
                (1, [(2, 5)], "fulfil", 10),
                (2, [(3, 2), (4, 2)], "fulfil", 6),
                (3, [(5, 3), (6, 1)], "fulfil", 2),
                (0, [(1, 2), (2, 2)], "confirm", 1),
                (1, [(0, 1)], "draft", 0),
            ]
            for number, (customer_index, lines, step, days_ago) in enumerate(plan, start=1):
                order = SalesOrder.objects.create(
                    number=f"SO-DEMO-{number:04d}",
                    customer=customers[customer_index],
                    actor=actor,
                    notes="Demo order",
                )
                for product_index, quantity in lines:
                    product = healthy[product_index % len(healthy)]
                    SalesOrderLine.objects.create(
                        order=order, product=product, quantity=quantity, unit_price=product.unit_price
                    )
                if step in {"confirm", "fulfil"}:
                    confirm_order(order_id=order.pk, actor=actor)
                if step == "fulfil":
                    fulfil_order(order_id=order.pk, actor=actor)
                    when = now - timedelta(days=days_ago)
                    SalesOrder.objects.filter(pk=order.pk).update(fulfilled_at=when)
                    StockMovement.objects.filter(sales_line__order=order).update(created_at=when)
                SalesOrder.objects.filter(pk=order.pk).update(
                    created_at=now - timedelta(days=days_ago, hours=3)
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Created 4 demo customers, 6 purchase orders and 6 sales orders."
            )
        )
