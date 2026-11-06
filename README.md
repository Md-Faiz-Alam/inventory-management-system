# Inventory Management System

A Django and PostgreSQL application for managing products, supplier contacts,
stock receipts and issues, movement history, and inventory reports.

## Current status

Functional local MVP with PostgreSQL tests and GitHub Actions. Public deployment
configuration and visitor demonstration access are still pending.

## Implemented features

- Login, POST-based logout, CSRF protection, and server-side permissions.
- Products with unique SKUs, categories, prices, search, and archive/restore.
- Supplier contacts and multiple supplier-product associations.
- Receipts and issues with an actor, reason, time, and running balance.
- Atomic stock updates, row locking, and duplicate-request protection.
- Insufficient-stock validation and supported quantity limits.
- Dashboard totals, low-stock alerts, and out-of-stock filters.
- Paginated stock/movement reports with filtered CSV downloads.
- Inclusive movement-date filters using Asia/Kolkata dates.
- Responsive layouts and light/dark themes.
- Repeatable synthetic demonstration data through `seed_demo`.

## Local setup: Windows PowerShell

Requirements: Python 3.11, Git, and Docker Desktop with Linux containers.
The supplied Compose file runs PostgreSQL 17. Stock operations require
PostgreSQL for row locking.

```powershell
git clone https://github.com/Md-Faiz-Alam/inventory-management-system.git
cd inventory-management-system

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
```

Edit `.env` to provide a new `DJANGO_SECRET_KEY` and local
`POSTGRES_PASSWORD`. For Compose, retain `POSTGRES_HOST=127.0.0.1`
and `POSTGRES_PORT=5433`. Use `DJANGO_DEBUG=true` for local development.
Keep `.env` out of Git.

Generate a new Django key with:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Start Docker Desktop, then:

```powershell
docker compose up -d --wait
if ($LASTEXITCODE -ne 0) { throw "PostgreSQL is not ready." }

python manage.py migrate
if ($LASTEXITCODE -ne 0) { throw "Migration failed." }

python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8001
```

Open http://127.0.0.1:8001/ and use your administrator account.

## Demonstration data

Replace `YOUR_ADMIN_USERNAME` with an existing authorized username:

```powershell
python manage.py seed_demo --actor "YOUR_ADMIN_USERNAME" --dry-run
python manage.py seed_demo --actor "YOUR_ADMIN_USERNAME"
```

| Item | Count |
|---|---:|
| Products | 32: 30 active, 2 archived |
| Suppliers | 6: 5 active, 1 archived |
| Movements | 256: 128 receipts, 128 issues |
| Supplier-product associations | 64 |
| Active demo products out of stock | 8 |
| Active demo products at low stock | 15 |
| Active demo products at healthy stock | 7 |
| Units across active demo products | 199 |

Search for `DEMO-V1-` to isolate demo products. The command reserves
`DEMO-V1-001` through `DEMO-V1-032` and supplier codes
`DEMO-SUP-V1-01` through `DEMO-SUP-V1-06`.

The history is synthetic and spans 28 days within the preceding month. Stock
is created through the transaction service; timestamps are assigned only to the
new simulated records. This dataset is for demonstrations, not performance claims.

The command creates no accounts or passwords. It requires an active actor with
product and supplier create/change permissions and both stock-operation permissions.

Rerunning with the same actor leaves a complete existing dataset unchanged,
including subsequent edits or stock activity. Identifier collisions, an incomplete
reserved dataset, or a different actor produce an error without changing records.
There is no automatic deletion or reset.

## Permissions

Grant permissions through Django administration. The staff flag alone does not
grant catalog, supplier, or stock permissions.

- Catalog, stock pages, and stock reports: `inventory.view_product`.
- Product creation: catalog viewing plus `inventory.add_product`.
- Product editing and status: catalog viewing plus `inventory.change_product`.
- Receipts/issues: catalog viewing plus `inventory.receive_stock` or
  `inventory.issue_stock`, respectively.
- Movement history/reports: catalog viewing plus `inventory.view_stockmovement`.
- Supplier viewing: `suppliers.view_supplier`.
- Supplier creation/editing: supplier viewing plus `suppliers.add_supplier`
  or `suppliers.change_supplier`, respectively.
- Supplier-product associations additionally require `inventory.view_product`.

## Verification

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --verbosity 2 --noinput
git diff --check
```

Run the complete suite against PostgreSQL, including concurrency and demo-command
integration tests. CI checks dependencies, migrations, tests, and static collection.

## Remaining work and limits

- Production server, static serving, database SSL, and HTTPS settings.
- Limited visitor-demo access and login rate limiting for public deployment.
- Clean-install verification, backup/restoration checks, screenshots, and presentation.
- Browser accessibility/mobile testing beyond server-side template tests.
- Product images, explicit returns/adjustments, and printable reports remain planned.

Stock reports cover active products; movement reports retain archived-product
history. Unit prices are current catalog prices, not historical purchase costs.
CSV downloads include all matching pages up to 10,000 rows; larger exports are
rejected. Formula-like text is apostrophe-prefixed; spreadsheet re-saving can
change interpretation.

Reports are live views. Summary and row queries do not promise a single consistent
snapshot during concurrent stock activity.

## Modules

| Module | Responsibility |
|---|---|
| accounts | User model and administration |
| inventory | Catalog, stock service, pages, demo command |
| suppliers | Supplier contacts and product associations |
| reports | Read-only reports and CSV output |
| config | Settings and URL routing |

The college-document `Report/` folder is separate from the Django `reports/`
application. Final submission packaging is assembled separately.