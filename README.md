# StockFlow: Inventory Management System

[![Django CI](https://github.com/Md-Faiz-Alam/inventory-management-system/actions/workflows/ci.yml/badge.svg)](https://github.com/Md-Faiz-Alam/inventory-management-system/actions/workflows/ci.yml)

StockFlow is a web-based inventory management system for small and medium businesses.
It keeps an exact, real-time record of every product and every unit that comes in or
goes out, with who made the change and why. On top of that record it manages suppliers,
purchase orders, customers and sales orders, produces PDF invoices and reports, and gives
managers a live dashboard of what needs reordering.

Built with Django and PostgreSQL as a BCA Semester 6 major project.

### Live demo

**https://stockflow-inventory-0qr7.onrender.com**

Click **Explore as guest** for a read-only tour of every page; no sign-up or password is
needed. The site runs on Render's free plan, which sleeps after 15 minutes without
visitors, so the first page can take up to a minute to load. If the site is unavailable,
the [screenshots](#screenshots) show every screen.

## Contents

- [Screenshots](#screenshots)
- [Features](#features)
- [How it works](#how-it-works)
- [Roles and access](#roles-and-access)
- [Architecture](#architecture)
- [Technology](#technology)
- [Run it locally](#run-it-locally)
- [Deploy for free (Render + Neon)](#deploy-for-free-render--neon)
- [REST API](#rest-api)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Design notes and limits](#design-notes-and-limits)
- [Author](#author)

## Screenshots

Screenshots of every main screen are in [`docs/screenshots/`](docs/screenshots/):

| Screen | Shows |
|---|---|
| [Landing page](docs/screenshots/01-landing-page.jpg) | Live figures and one-click guest access |
| [Dashboard](docs/screenshots/02-dashboard.png) | Key figures and charts |
| [Stock control](docs/screenshots/03-stock-control.png) | Health badges, receive/issue/count and reorder shortcuts |
| [Purchase order](docs/screenshots/04-purchase-order.png) | Partial deliveries, progress and PDF download |
| [Tax invoice (PDF)](docs/screenshots/05-tax-invoice-pdf.png) | Invoice generated for a fulfilled sales order |
| [Activity log](docs/screenshots/06-activity-log.png) | Who changed what, with old and new values |
| [Users and roles](docs/screenshots/07-users-and-roles.png) | Admin, Manager, Staff and read-only Viewer |
| [REST API docs](docs/screenshots/08-rest-api-docs.png) | Interactive Swagger documentation |
| [Sign-in](docs/screenshots/09-sign-in.jpg) | Sign-in with a read-only guest option |
| [Mobile view](docs/screenshots/10-mobile-dashboard.png) | Dashboard on a phone |

## Features

**Inventory**
- Product catalogue with unique SKUs, categories, unit prices and reorder levels.
  Products are archived, never deleted, so their history is kept.
- Receive and issue stock through one transactional service. Stock can never go negative,
  and a double click or page refresh does not record the same movement twice.
- Complete movement history with the user, reason and balance before and after.
- Corrections without editing history: **reverse** a movement (an exact opposite entry)
  or record a **stocktake** that adjusts stock to a physical count.
- Low-stock and out-of-stock indicators, plus a top-bar alert that refreshes every minute.

**Purchasing and sales**
- Suppliers linked to the products they provide.
- Purchase orders to suppliers, received in one or several deliveries with a delivery
  document number. Open orders can be cancelled.
- Customers and multi-line sales orders that move from Draft → Confirmed → Fulfilled.
  Fulfilment issues stock for every line in one transaction; if any line is short,
  nothing is issued.

**Insights and documents**
- Dashboard with stock value, product and unit totals, low and out-of-stock counts, open
  purchase orders and sales this month, plus charts: stock in vs. out (last 30 days),
  stock health, stock value by category and the fastest-moving products.
- Stock report and movement report with search and date filters, downloadable as CSV or PDF.
- PDF purchase orders, and PDF tax invoices (fulfilled orders) or quotations (open orders).

**Access and security**
- Four roles (Admin, Manager, Staff, Viewer) and a one-click, read-only guest login.
- User management: create users, change roles, deactivate accounts and set new passwords.
- Account lock for 15 minutes after five failed sign-ins, on the sign-in page and the API.
- Activity log of every create, edit, status change, stock operation, order step and
  sign-in, with old and new values and the IP address.
- Protection against two people overwriting each other's edits on the same record.
- HTTPS only, secure cookies, CSRF protection and security headers in production.

**Interface and integration**
- Live search and filtering without page reloads (HTMX) on every list.
- Responsive layout for desktop and mobile, with light and dark themes.
- Public landing page showing live figures from the database.
- REST API with token sign-in and interactive Swagger documentation.

## How it works

A typical day in StockFlow follows the path of the goods:

1. **Catalogue:** a Manager adds products (SKU, price, reorder level) and suppliers, and
   links each supplier to the products it provides.
2. **Spot what is running low:** the dashboard, the alert bell and the stock page show
   every product at or below its reorder level.
3. **Purchase:** from a low-stock product the Manager clicks *Reorder*. The purchase order
   form is pre-filled with a linked supplier, the catalogue price and a suggested quantity.
   The order can be downloaded as a PDF.
4. **Receive:** when the delivery arrives, Staff record it against the purchase order with
   the delivery document number. Stock goes up and the order shows what is still
   outstanding; partial deliveries are allowed.
5. **Sell:** a sales order is created for a customer, confirmed by a Manager and fulfilled
   by Staff, which issues the stock for every line. A tax invoice PDF is then available.
6. **Correct:** a wrong entry is fixed by reversing it, and a physical count is entered as a
   stocktake. The original records always stay in the history.
7. **Review:** reports, the movement history and the activity log show exactly what
   happened, when, and by whom.

Every stock change in steps 4–6, from a page, a purchase order, a sales order or the API,
goes through `inventory.services.record_stock_movement`. It locks the product row
(`SELECT … FOR UPDATE`), checks the rules and permissions, updates the balance and writes
a movement record in the same transaction, so balances stay correct when several people
work at once.

## Roles and access

| Capability | Viewer | Staff | Manager | Admin |
|---|:-:|:-:|:-:|:-:|
| View products, stock, movements, suppliers, customers, orders and reports | ✓ | ✓ | ✓ | ✓ |
| Receive and issue stock; record deliveries against purchase orders | | ✓ | ✓ | ✓ |
| Create sales order drafts; fulfil confirmed sales orders | | ✓ | ✓ | ✓ |
| Add and edit products, suppliers and customers | | | ✓ | ✓ |
| Raise and cancel purchase orders; confirm and cancel sales orders | | | ✓ | ✓ |
| Reverse movements and record stocktakes | | | ✓ | ✓ |
| View the activity log | | | ✓ | ✓ |
| Manage users and roles | | | | ✓ |

- Roles are Django groups with fixed permissions, defined in `accounts/roles.py` and
  re-applied after every `migrate`. Superusers have full access, including `/admin/`.
- **Guest:** the *Explore as guest* button signs visitors in as the shared `demo_guest`
  account, which has the Viewer role and no password, so it cannot be used any other way.
  Guest sessions last two hours. Guests cannot see the activity log or user management.
  The button appears only when `GUEST_ACCESS` is on and the guest account exists.
- **Demo accounts** (`demo_admin`, `demo_manager`, `demo_staff`) exist only if you set
  their passwords (see [Demo data](#demo-data)). While demo mode is on, their profiles and
  passwords cannot be changed by visitors, and only a superuser can edit them.
- The public deployment offers **guest access only**: visitors can look at everything, and
  only the owner's superuser account can change data.

## Architecture

```
Browser ── HTML pages + HTMX + Chart.js ──► Django views ─┐
Other programs ── REST API (DRF, token) ──► API views ────┤
                                                          ▼
                     Service layer: inventory.services.record_stock_movement
                                    sales.services.fulfil_order
                     (row locking, business rules, idempotent operation IDs)
                                                          │
                                                          ▼
                                                     PostgreSQL
```

| App | Responsibility |
|---|---|
| `accounts` | Custom user model, roles, sign-in with lockout, guest sign-in, profile, user management |
| `inventory` | Products, stock balances and movements, the stock service, purchase orders, corrections, dashboard, demo-data commands |
| `suppliers` | Supplier contacts and supplier–product links |
| `sales` | Customers, sales orders and the fulfilment service |
| `reports` | Stock and movement reports as HTML, CSV and PDF |
| `audit` | Activity log model, logging helpers and sign-in signals |
| `api` | REST API, permissions and OpenAPI schema |
| `config` | Settings, URL routing, HTMX and PDF helpers, edit-conflict protection |

Main tables: `accounts_user`, `inventory_product`, `inventory_stockbalance`,
`inventory_stockmovement`, `inventory_purchaseorder`, `suppliers_supplier`,
`sales_customer`, `sales_salesorder`, `sales_salesorderline`, `audit_auditentry`.

## Technology

| Layer | Technology |
|---|---|
| Language and framework | Python 3.11, Django 5.2 |
| Database | PostgreSQL 17 (Docker Compose locally, Neon in production) |
| Frontend | Django templates, HTML, CSS, JavaScript, HTMX 2, Chart.js 4 |
| API | Django REST Framework, drf-spectacular (OpenAPI / Swagger) |
| Documents | xhtml2pdf (PDF), Python `csv` (CSV) |
| Serving | Gunicorn, WhiteNoise |
| Testing and CI | Django test framework, GitHub Actions |
| Hosting | Render (web service) and Neon (PostgreSQL) |

Exact package versions are pinned in `requirements.txt`.

## Run it locally

Requirements: Python 3.11, Git and Docker Desktop. The commands are for Windows PowerShell.

```powershell
git clone https://github.com/Md-Faiz-Alam/inventory-management-system.git
cd inventory-management-system

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set a new `DJANGO_SECRET_KEY` and choose a `POSTGRES_PASSWORD`. Keep
`DJANGO_DEBUG=true` for local development. To generate a secret key:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Start PostgreSQL (Docker Desktop must be running), create the tables and an admin
account, then run the server:

```powershell
docker compose up -d --wait
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8001
```

Open http://127.0.0.1:8001/ and sign in with the account you just created.

### Demo data

`bootstrap_demo` prepares a demonstration and is safe to run any number of times:

```powershell
python manage.py bootstrap_demo
```

It reads these settings from `.env` (all optional):

| Setting | Effect |
|---|---|
| `DEMO_MODE=true` | Turns on demo mode: demo accounts that have a password are offered on the sign-in page, and demo accounts are protected |
| `GUEST_ACCESS` | Creates the read-only `demo_guest` account and shows the *Explore as guest* button. Defaults to the value of `DEMO_MODE` |
| `DEMO_ADMIN_PASSWORD`, `DEMO_MANAGER_PASSWORD`, `DEMO_STAFF_PASSWORD` | Creates or updates `demo_admin`, `demo_manager`, `demo_staff` with that password. A demo account whose password is removed is deactivated |
| `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_PASSWORD` | Creates this superuser once; later runs never change it |

When `demo_admin`, `demo_manager` or the superuser above is available, it also loads the
sample data once:
32 products, 6 suppliers and 256 stock movements spread over four weeks, then 4 customers,
6 purchase orders and 6 sales orders. If the sample catalogue already exists it is reused.
The command never deletes records. (`python manage.py seed_demo --actor <username>`
loads only the catalogue and movement history.)

## Deploy for free (Render + Neon)

Render runs the web app on its free plan and Neon provides a free PostgreSQL database.

1. **Database:** sign up at [neon.tech](https://neon.tech), create a project in the
   *AWS US West 2 (Oregon)* region, open **Connect**, turn connection pooling off and copy
   the connection string (`postgresql://…?sslmode=require`).
2. **Code:** push this repository to your GitHub account.
3. **Web service:** sign up at [render.com](https://render.com), choose
   **New → Blueprint** and select the repository. Render reads `render.yaml`.
4. **Secrets:** Render asks for three values:
   - `DATABASE_URL`: the Neon connection string
   - `DJANGO_SUPERUSER_USERNAME` and `DJANGO_SUPERUSER_PASSWORD`: your private admin account
5. **Deploy:** `build.sh` installs the packages, collects static files, runs migrations,
   creates the cache table and runs `bootstrap_demo`. Gunicorn then serves the site at
   `https://<service-name>.onrender.com`. Every push to `main` redeploys automatically.

`render.yaml` sets the rest: a generated `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`,
`DEMO_MODE=true` and `GUEST_ACCESS=true`. Render's own host name is added to the allowed
hosts and trusted origins automatically; a custom domain must be added to
`DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`.

**If the site does not load:** the free plan sleeps after 15 minutes without visitors and
usually wakes within a minute. If it stays unavailable, open the service in the Render
dashboard and choose **Manual Deploy → Deploy latest commit**. The data in Neon is kept.

## REST API

Interactive documentation: [`/api/docs/`](https://stockflow-inventory-0qr7.onrender.com/api/docs/)
(OpenAPI schema at `/api/schema/`). All endpoints need a signed-in user with the right role.

| Endpoint | Methods | Notes |
|---|---|---|
| `/api/v1/auth/token/` | POST | Exchange a username and password for a token (same lockout as the sign-in page) |
| `/api/v1/products/` | GET, POST, PUT, PATCH | Each product includes `on_hand` and `stock_status`; `?search=` and `?active=` filters |
| `/api/v1/suppliers/`, `/api/v1/customers/` | GET, POST, PUT, PATCH | |
| `/api/v1/stock-movements/` | GET, POST | POST records a receipt or issue through the stock service |
| `/api/v1/purchase-orders/` | GET | Read-only |
| `/api/v1/sales-orders/` | GET | Read-only; `POST /api/v1/sales-orders/{id}/fulfil/` fulfils a confirmed order |
| `/api/v1/summary/` | GET | Stock totals, dashboard figures and chart data |

```bash
# Get a token
curl -X POST -d "username=YOUR_USERNAME&password=YOUR_PASSWORD" \
  https://stockflow-inventory-0qr7.onrender.com/api/v1/auth/token/

# Search products
curl -H "Authorization: Token YOUR_TOKEN" \
  "https://stockflow-inventory-0qr7.onrender.com/api/v1/products/?search=lamp"

# Issue stock (sending the same operation_id again does not issue twice)
curl -X POST -H "Authorization: Token YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"product": 4, "kind": "issue", "quantity": 2, "reason": "Online order 1042", "operation_id": "6f1c0a7e-1b2c-4d3e-9f00-123456789abc"}' \
  https://stockflow-inventory-0qr7.onrender.com/api/v1/stock-movements/
```

Records are archived rather than deleted, so the API offers no DELETE. Requests are
limited to 600 per hour per user. The guest account has no password and therefore
cannot obtain a token.

## Testing

### Automated tests

217 tests run against PostgreSQL. They cover the stock service (row locking, simultaneous
requests, repeated submissions), purchase and sales workflows, permissions for every
role, guest access, sign-in lockout, the activity log, the API, reports, PDFs, CSV
exports and the live-search partials.

```powershell
python manage.py test --noinput             # all tests
python manage.py test --noinput -v 2 sales  # one app, listing each test
```

### Continuous integration

GitHub Actions runs on every push and pull request to `main` against PostgreSQL 17:
system checks, a missing-migrations check, migrations on a fresh database, all tests,
static file collection, `bootstrap_demo` twice (it must change nothing the second time),
Django's production deployment checks and a start-up test of Gunicorn.

### Website check

`scripts/check_site.py` signs in like a visitor and opens every page, live-search result,
report export, PDF, API endpoint and static file, then prints PASS, SKIP or FAIL for each.
It only reads data, so it is safe to run against the live site. Pages the account's role
may not open are reported as SKIP.

```powershell
# As the read-only guest (no password needed)
python scripts/check_site.py --base https://stockflow-inventory-0qr7.onrender.com --guest

# As your own account (asks for the password)
python scripts/check_site.py --base http://127.0.0.1:8001 --username YOUR_USERNAME
```

### Before deploying

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
$env:DJANGO_DEBUG="false"; $env:DJANGO_SECURE_HTTPS="true"; python manage.py check --deploy
```

## Project structure

```
inventory-management-system/
├── accounts/            users, roles, sign-in, guest access, user management
├── api/                 REST API
├── audit/               activity log
├── config/              settings, URLs, shared helpers
├── inventory/           products, stock service, purchase orders, dashboard, demo commands
├── reports/             reports and exports
├── sales/               customers and sales orders
├── suppliers/           suppliers
├── templates/           HTML templates (pages, partials, PDF layouts)
├── static/              CSS, JavaScript, bundled htmx and Chart.js
├── scripts/check_site.py
├── docs/screenshots/    images used in this README
├── .github/workflows/   CI pipeline
├── build.sh             Render build steps
├── render.yaml          Render Blueprint
├── compose.yaml         local PostgreSQL
├── requirements.txt     pinned dependencies
└── manage.py
```

## Design notes and limits

- The stock report covers active products; the movement report also keeps the history of
  archived products. Report rows, totals and page counts are read in a single query so
  they always agree.
- Unit prices in the catalogue are current prices; the agreed cost is stored with each
  purchase order and its receipts.
- CSV exports are limited to 10,000 rows and PDF reports to 500 rows. Text that a
  spreadsheet could treat as a formula is prefixed with an apostrophe.
- Edit forms carry a signed version token: saving a record that someone else changed in
  the meantime is refused with a message instead of overwriting their work.
- Sign-in lockout counts failures per username and client IP. In production the counters
  are kept in a database cache table shared by all server processes, and the client IP is
  taken from Cloudflare's `True-Client-IP` header on Render rather than the spoofable
  `X-Forwarded-For`.
- A stock issue that fulfilled a sales order cannot be reversed from the movement list;
  returned goods are received back as stock with a reason, so the order history stays
  accurate.
- The free Render plan sleeps when idle, so the first request after a quiet period is slow.

## Author

**Md Faiz Alam**, BCA (Bachelor of Computer Applications), Shoolini University

GitHub: [@Md-Faiz-Alam](https://github.com/Md-Faiz-Alam)
