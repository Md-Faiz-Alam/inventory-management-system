# Inventory Management System

A web application for small businesses to manage products, track stock
movements, identify low-stock items, and review inventory reports.

## Project status

Initial repository setup. Application features are not implemented yet.

## Planned features

- Admin and staff authentication with role-based permissions.
- Product management with unique SKUs, categories, and product images.
- Supplier records linked to products.
- Stock receipts, issues, returns, and adjustments.
- Transaction history recording the user, time, quantity, and reason.
- Dashboard with inventory totals and low-stock alerts.
- Search, filtering, CSV exports, and printable reports.
- Responsive pages for desktop and mobile devices.

## Core requirements

- Stock quantities must never become negative.
- Every stock quantity change must create a transaction record.
- Related stock changes must succeed or fail together.
- Concurrent operations must preserve correct stock balances.
- Permissions must be enforced on the server.
- Secrets and local databases must remain outside version control.

## Intended demonstration

Create a product, receive stock, issue stock, trigger a low-stock alert,
inspect the transaction history, and export a report.
