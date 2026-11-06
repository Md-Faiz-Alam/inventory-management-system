"""Create a versioned, additive demonstration dataset."""

from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from inventory.models import Product, StockBalance, StockMovement
from inventory.services import record_stock_movement
from suppliers.models import Supplier

MARKER = "Synthetic demonstration data: inventory-demo-v1."
LOCK_ID = 741_926_315

REQUIRED_PERMISSIONS = [
    "inventory.view_product",
    "inventory.add_product",
    "inventory.change_product",
    "inventory.receive_stock",
    "inventory.issue_stock",
    "suppliers.view_supplier",
    "suppliers.add_supplier",
    "suppliers.change_supplier",
]

CATALOG = [
    ("USB-C Cable", "Electronics", "299.00"),
    ("Wireless Mouse", "Electronics", "799.00"),
    ("Keyboard", "Electronics", "1299.00"),
    ("USB Hub", "Electronics", "899.00"),
    ("Power Bank", "Electronics", "1499.00"),
    ("Phone Stand", "Electronics", "249.00"),
    ("Notebook Pack", "Office", "180.00"),
    ("Ballpoint Pen Box", "Office", "120.00"),
    ("Printer Paper", "Office", "350.00"),
    ("File Folder", "Office", "45.00"),
    ("Desk Organizer", "Office", "399.00"),
    ("Stapler", "Office", "160.00"),
    ("LED Desk Lamp", "Home", "999.00"),
    ("Storage Box", "Home", "450.00"),
    ("Water Bottle", "Home", "320.00"),
    ("Wall Clock", "Home", "650.00"),
    ("Cleaning Cloth", "Home", "90.00"),
    ("Screwdriver Set", "Hardware", "550.00"),
    ("Measuring Tape", "Hardware", "199.00"),
    ("Safety Gloves", "Hardware", "150.00"),
    ("Cable Tie Pack", "Hardware", "110.00"),
    ("Padlock", "Hardware", "275.00"),
    ("Carton Box", "Packaging", "65.00"),
    ("Bubble Wrap", "Packaging", "225.00"),
    ("Shipping Tape", "Packaging", "75.00"),
    ("Mailing Envelope", "Packaging", "20.00"),
    ("Label Roll", "Packaging", "180.00"),
    ("Floor Cleaner", "Cleaning", "210.00"),
    ("Hand Soap", "Cleaning", "95.00"),
    ("Microfiber Cloth", "Cleaning", "130.00"),
    ("Scrub Brush", "Cleaning", "85.00"),
    ("Refuse Bag Pack", "Cleaning", "140.00"),
]


def demo_plan():
    """Return deterministic product and operation specifications."""
    products = []

    for index, (name, category, price) in enumerate(CATALOG):
        number = index + 1
        sku = f"DEMO-V1-{number:03d}"
        archived = number > 30
        target = 0 if archived else (0, 3, 5, 20)[index % 4]
        movements = []

        operations = [
            ("receive", 40 + number),
            ("issue", 5),
            ("receive", 10),
            ("issue", 4),
            ("receive", 8),
            ("issue", 3),
            ("receive", 6),
            ("issue", 52 + number - target),
        ]

        for step, (kind, quantity) in enumerate(operations):
            movements.append(
                {
                    "kind": kind,
                    "quantity": quantity,
                    "operation_id": uuid5(
                        NAMESPACE_URL,
                        f"https://inventory-demo.invalid/v1/{sku}/{step}",
                    ),
                    "reason": (
                        f"Demo simulation {number:03d}, "
                        f"step {step + 1}: {kind}"
                    ),
                    "day": step * 4,
                }
            )

        products.append(
            {
                "sku": sku,
                "name": f"Demo {name}",
                "category": category,
                "unit_price": Decimal(price),
                "target": target,
                "archived": archived,
                "movements": movements,
            }
        )

    return products


class Command(BaseCommand):
    help = "Create 32 demo products, 6 suppliers, and 256 simulated movements."
    requires_migrations_checks = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--actor",
            required=True,
            help="Existing authorized username.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Inspect without writing records.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError(
                "Demo stock creation requires PostgreSQL."
            )

        actor = (
            get_user_model()
            .objects.filter(username=options["actor"])
            .first()
        )

        if (
            actor is None
            or not actor.is_active
            or not actor.has_perms(REQUIRED_PERMISSIONS)
        ):
            raise CommandError(
                "Choose an existing active user with product, supplier, "
                "receive, and issue permissions."
            )

        plan = demo_plan()
        codes = [
            f"DEMO-SUP-V1-{number:02d}"
            for number in range(1, 7)
        ]
        expected_operations = {
            movement["operation_id"]: item["sku"]
            for item in plan
            for movement in item["movements"]
        }

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    [LOCK_ID],
                )

            products = list(
                Product.objects.annotate(
                    key=Lower("sku")
                ).filter(
                    key__in=[
                        item["sku"].lower()
                        for item in plan
                    ]
                )
            )
            suppliers = list(
                Supplier.objects.annotate(
                    key=Lower("code")
                ).filter(
                    key__in=[
                        code.lower()
                        for code in codes
                    ]
                )
            )
            movements = list(
                StockMovement.objects.filter(
                    operation_id__in=expected_operations
                ).values(
                    "operation_id",
                    "actor_id",
                    "product__sku",
                )
            )

            if products or suppliers or movements:
                complete = (
                    len(products) == len(plan)
                    and len(suppliers) == len(codes)
                    and len(movements) == len(expected_operations)
                    and all(
                        product.description == MARKER
                        for product in products
                    )
                    and all(
                        supplier.notes == MARKER
                        for supplier in suppliers
                    )
                    and all(
                        row["actor_id"] == actor.pk
                        and row["product__sku"]
                        == expected_operations[row["operation_id"]]
                        for row in movements
                    )
                )

                if not complete:
                    raise CommandError(
                        "Reserved demo identifiers already exist or the "
                        "demo actor differs. No records were changed."
                    )

                self.stdout.write(
                    "Demo dataset already exists. "
                    "No records were changed."
                )
                return

            if options["dry_run"]:
                self.stdout.write(
                    "Would create 32 products "
                    "(30 active, 2 archived), 6 suppliers, and "
                    "256 simulated movements. "
                    "No records were changed."
                )
                return

            base = timezone.now() - timedelta(days=30)
            created_suppliers = []

            for number, code in enumerate(codes, start=1):
                supplier = Supplier(
                    name=f"Demo Supply Partner {number:02d}",
                    code=code,
                    contact_name=f"Demo Contact {number:02d}",
                    email=(
                        f"demo-supplier-{number:02d}@example.com"
                    ),
                    address=(
                        "Synthetic address, Demo Business Park, "
                        "Sample City"
                    ),
                    notes=MARKER,
                    is_active=number != 6,
                )
                supplier.full_clean()
                supplier.save()

                Supplier.objects.filter(
                    pk=supplier.pk
                ).update(
                    created_at=base - timedelta(days=1)
                )
                created_suppliers.append(supplier)

            for index, item in enumerate(plan):
                product = Product(
                    name=item["name"],
                    sku=item["sku"],
                    category=item["category"],
                    unit_price=item["unit_price"],
                    reorder_level=5,
                    description=MARKER,
                )
                product.full_clean()
                product.save()

                Product.objects.filter(
                    pk=product.pk
                ).update(
                    created_at=base - timedelta(hours=1)
                )

                created_suppliers[
                    index % 5
                ].products.add(product)

                created_suppliers[
                    index % 5 + 1
                ].products.add(product)

                recorded_at = base

                for movement in item["movements"]:
                    result = record_stock_movement(
                        product_id=product.pk,
                        actor=actor,
                        kind=movement["kind"],
                        quantity=movement["quantity"],
                        reason=movement["reason"],
                        operation_id=movement["operation_id"],
                    )
                    recorded_at = base + timedelta(
                        days=movement["day"],
                        hours=index % 6,
                    )
                    StockMovement.objects.filter(
                        pk=result.movement.pk
                    ).update(created_at=recorded_at)

                balance = StockBalance.objects.get(
                    product=product
                )

                if balance.quantity != item["target"]:
                    raise CommandError(
                        "Demo balance verification failed; "
                        "the transaction was rolled back."
                    )

                StockBalance.objects.filter(
                    pk=balance.pk
                ).update(
                    updated_at=recorded_at
                )

                if item["archived"]:
                    product.is_active = False
                    product.save(
                        update_fields=[
                            "is_active",
                            "updated_at",
                        ]
                    )

        self.stdout.write(
            self.style.SUCCESS(
                "Created 32 demo products, 6 demo suppliers, "
                "and 256 simulated stock movements. "
                "Existing business records were preserved."
            )
        )