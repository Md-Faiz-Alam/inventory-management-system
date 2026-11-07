# StockFlow: Inventory Management System

A full-stack inventory management web application built with Django and PostgreSQL.
It covers the whole stock lifecycle: products, stock control, purchase orders to
suppliers, sales orders to customers, analytics, role-based access, an audit trail,
PDF documents and a REST API.

BCA Semester 6 major project.

## Highlights

| Area | What it does |
|---|---|
| Dashboard | KPI cards (stock value, low/out of stock, open POs, monthly sales), Chart.js charts (30-day stock flow, stock health, value by category, fastest movers), low-stock alert bell that refreshes every minute |
| Products & stock | Catalog with unique SKUs and archive/restore; receive and issue stock with row locking, duplicate-submit protection and running balances |
| Corrections | Reverse any movement with an exact opposite record; stocktake adjusts stock to a physical count |
| Purchasing | Purchase orders to suppliers, partial deliveries against them, cancellation, PDF purchase order |
| Sales | Customers, multi-line sales orders (draft → confirmed → fulfilled), atomic fulfilment that rolls back on any shortage, PDF quotation/invoice |
| Roles & users | Admin, Manager and Staff roles; user management; profile and password change; login lockout after repeated failures |
| Audit log | Every create, edit, status change, stock operation, order step and sign-in, with field-level changes and IP |
| Reports | Stock and movement reports with filters, CSV export (up to 10,000 rows) and PDF export |
| REST API | Products, suppliers, customers, stock movements, purchase and sales orders; token or session auth; Swagger docs at `/api/docs/` |
| UI | Live search and filtering with HTMX, responsive layout, light/dark themes, public landing page |
| Ops | Health check, WhiteNoise static files, HTTPS hardening, CI on PostgreSQL 17, one-click Render blueprint |

## Technology

Python 3.11 · Django 5.2 · PostgreSQL 17 · HTMX 2 · Chart.js 4 ·
Django REST Framework · drf-spectacular · xhtml2pdf · WhiteNoise · Gunicorn ·
GitHub Actions · Render (web) + Neon (database).

## Architecture

```
Browser ── HTMX / Chart.js ──► Django views (server-rendered templates)
                                    │
             REST API (DRF) ───────►│
                                    ▼
                       Service layer (single entry point for stock)
                       inventory.services.record_stock_movement
                       sales.services.fulfil_order
                                    │  SELECT … FOR UPDATE, idempotent operation IDs
                                    ▼
                               PostgreSQL
```

Stock is never edited directly. Receipts, issues, PO deliveries, reversals, stocktakes,
sales fulfilment and API calls all go through `record_stock_movement`, which locks
the product row, checks the business rules, writes an immutable movement with the
balance before and after, and safely ignores a retried request.

| App | Responsibility |
|---|---|
| `accounts` | User model, roles (`roles.py`), sign-in with lockout, profile, user management |
| `inventory` | Catalog, stock service, purchase orders, corrections, dashboard, demo commands |
| `suppliers` | Supplier contacts and product associations |
| `sales` | Customers, sales orders and the fulfilment service |
| `reports` | HTML, CSV and PDF reports |
| `audit` | Activity log model, helpers and sign-in signals |
| `api` | REST API and OpenAPI schema |
| `config` | Settings, URLs, HTMX/PDF helpers, edit-conflict protection |

## Roles

| Capability | Viewer | Staff | Manager | Admin |
|---|:-:|:-:|:-:|:-:|
| View catalog, stock, movements, suppliers, customers, orders, reports | ✓ | ✓ | ✓ | ✓ |
| Receive and issue stock, receive PO deliveries | | ✓ | ✓ | ✓ |
| Create sales order drafts, fulfil confirmed orders | | ✓ | ✓ | ✓ |
| Add/edit products, suppliers, customers | | | ✓ | ✓ |
| Raise/cancel purchase orders, confirm/cancel sales orders | | | ✓ | ✓ |
| Reverse movements, stocktakes | | | ✓ | ✓ |
| Activity log | | | ✓ | ✓ |
| Manage users and roles | | | | ✓ |

Roles are Django groups that are re-synchronised after every `migrate`
(see `accounts/roles.py`). Superusers keep full access.

## Local setup (Windows PowerShell)

Requirements: Python 3.11, Git, Docker Desktop.

```powershell
git clone https://github.com/Md-Faiz-Alam/inventory-management-system.git
cd inventory-management-system

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set a new `DJANGO_SECRET_KEY` and a local `POSTGRES_PASSWORD`, keep
`DJANGO_DEBUG=true`. Generate a key with:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Then start PostgreSQL and the app:

```powershell
docker compose up -d --wait
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8001
```

Open http://127.0.0.1:8001/.

### Demo data

Set `DEMO_ADMIN_PASSWORD`, `DEMO_MANAGER_PASSWORD` and `DEMO_STAFF_PASSWORD`
(and `DEMO_MODE=true` to show them on the login page) in `.env`, then:

```powershell
python manage.py bootstrap_demo
```

This creates the users `demo_admin`, `demo_manager` and `demo_staff`, 32 products,
6 suppliers, 256 historical movements, 4 customers, 6 purchase orders and
6 sales orders. It is safe to run repeatedly: existing data is never changed or deleted.
`seed_demo --actor <username>` still loads only the catalog and movement history.

## Deploying for free (Render + Neon)

Render hosts the Django app on its free plan and Neon provides a free PostgreSQL
database that does not expire.

1. **Create the database.** Sign up at [neon.tech](https://neon.tech), create a project
   (region: Asia Pacific / Singapore), and copy the connection string. It looks like
   `postgresql://user:password@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`.
2. **Push the code to GitHub.**
3. **Create the web service.** Sign up at [render.com](https://render.com), choose
   **New → Blueprint**, pick this repository. Render reads `render.yaml`.
4. **Fill in the secret values** Render asks for:
   - `DATABASE_URL`: the Neon connection string.
   - `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`: **your private admin
     account**. Use a strong password you keep to yourself. It is created on the first
     deploy only; later deploys never change it.
   That is all. Visitors get a one-click, view-only **Explore as guest** button; only your
   account can change data. Demo data is loaded under your account on the first deploy.
5. **Deploy.** `build.sh` installs packages, collects static files, runs migrations and
   `bootstrap_demo`. Gunicorn then serves the app at `https://<name>.onrender.com`.

Notes:

- The free Render plan sleeps after 15 minutes without traffic. The first request after
  that takes about a minute. Open the site a few minutes before a demonstration.
- `DJANGO_SECRET_KEY` is generated by Render. `RENDER_EXTERNAL_HOSTNAME` is added to
  `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` automatically.
- To use a custom domain, add it to `DJANGO_ALLOWED_HOSTS` and
  `DJANGO_CSRF_TRUSTED_ORIGINS` (with `https://`).

## Visitor access

The public site is **view-only for visitors**:

| Who | How they get in | What they can do |
|---|---|---|
| Anyone | Nothing needed | Landing page, live totals, API docs |
| Guest | **Explore as guest** button, no password | Open every operational page, charts, reports and PDFs; change nothing |
| You | Your own superuser account | Everything, including `/admin/` |

Manager and Staff demo logins are **off** unless you set `DEMO_MANAGER_PASSWORD` /
`DEMO_STAFF_PASSWORD`. If one of those is removed later, the next deploy deactivates
that account, so a password that was once public stops working.

The guest account (`demo_guest`) has no password, so it can only be used through the
button, which signs in exactly that read-only account. Guests cannot see the activity
log (it contains IP addresses) or user management. Demo accounts cannot change their
own password or profile, and no demo account can edit a superuser, so your admin account
stays as you left it. Turn the guest button off with `GUEST_ACCESS=false`.

Admins can also give real users the read-only **Viewer** role.

## REST API

Interactive documentation: `/api/docs/` (OpenAPI schema at `/api/schema/`).

```bash
# get a token
curl -X POST -d "username=demo_manager&password=..." https://<host>/api/v1/auth/token/

# search products (each row includes on_hand and stock_status)
curl -H "Authorization: Token <key>" "https://<host>/api/v1/products/?search=demo"

# issue stock (repeat-safe: same operation_id is applied once)
curl -X POST -H "Authorization: Token <key>" -H "Content-Type: application/json" \
  -d '{"product": 4, "kind": "issue", "quantity": 2, "reason": "Online order 1042", "operation_id": "6f1c0a7e-1b2c-4d3e-9f00-123456789abc"}' \
  https://<host>/api/v1/stock-movements/
```

Records are archived rather than deleted, so the API has no DELETE.

## Testing

### 1. Automated tests (about 200, against PostgreSQL)

```powershell
python manage.py test --noinput            # everything
python manage.py test --noinput -v 2 sales # one app, with test names
```

They cover the stock engine (locking, concurrency, retries), purchase and sales
workflows, roles, lockout, audit trail, API permissions, reports, PDFs and HTMX partials.

### 2. End-to-end website check

`scripts/check_site.py` signs in like a visitor and opens every page, live-search
partial, report export, PDF, API endpoint and static file, then prints PASS/FAIL.
It only reads data, so it is safe on the live site.

```powershell
python manage.py runserver 127.0.0.1:8001     # in one terminal
python scripts/check_site.py --base http://127.0.0.1:8001 --username demo_admin
python scripts/check_site.py --base https://<your-app>.onrender.com --username demo_staff
```

Run it once per role: pages a role may not open are reported as SKIP (403), not FAIL.

### 3. Before deploying

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
$env:DJANGO_DEBUG="false"; $env:DJANGO_SECURE_HTTPS="true"; python manage.py check --deploy
python manage.py collectstatic --noinput
```

CI runs all of this plus `bootstrap_demo` twice and a Gunicorn smoke test on every push.

### VS Code

`.vscode/` holds ready-made **tasks** (Terminal → Run Task: start DB, migrate + demo
data, run website, run tests, check website, pre-deploy checks) and **debug
configurations** (Run and Debug: debug the website, all tests, the tests in the open
file, or the website checker). It also points VS Code at `.venv`, so imports resolve.
The folder is git-ignored; remove `.vscode/` from `.gitignore` to share it.

## Design notes and limits

- Stock reports cover active products; movement reports keep archived-product history.
  Unit prices are current catalog prices; purchase costs are stored on each PO receipt.
- Report pages, totals and pagination come from one SQL snapshot.
- CSV exports are capped at 10,000 rows and PDF reports at 500 rows.
  Formula-like text in CSV cells is apostrophe-prefixed.
- Edits to products, suppliers and customers carry a signed version token. Saving a form
  that someone else changed in the meantime is refused instead of overwriting their work.
- Sign-in lockout (page and API token) counts failures per username and client IP.
  In production the counters live in a database cache table shared by all Gunicorn
  workers. The client IP comes from Cloudflare's `True-Client-IP` on Render, never
  from the spoofable `X-Forwarded-For`.
- In demo mode the shared `demo_*` accounts are read-only (no password or profile
  changes, cannot be edited by other users), so visitors cannot lock each other out.
- A sales fulfilment cannot be reversed from the movement list; returned goods are
  received as stock with a reason, so the order history stays truthful.

The college `Report/` folder is separate from the Django `reports/` app.
