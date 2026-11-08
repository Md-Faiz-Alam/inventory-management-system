"""Check a running StockFlow site end to end, like a visitor would.

Works against your local server or the deployed site. It only reads pages
(GET requests plus sign-in/sign-out), so it is safe to run on production.

Usage:
    python scripts/check_site.py --base http://127.0.0.1:8000 --username demo_admin --password ...
    python scripts/check_site.py --base https://stockflow-inventory-vtqp.onrender.com -u demo_manager
    python scripts/check_site.py --base http://127.0.0.1:8000 --guest

The password can also come from the CHECK_PASSWORD environment variable.
Exit code is 0 when every check passes, 1 otherwise. Needs only the standard library.
"""

import argparse
import getpass
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class SiteUnreachable(Exception):
    """The site did not answer at all (not started, wrong address, no network)."""


class Checker:
    def __init__(self, base, timeout):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.results = []  # (status, name, detail)

    # ------------------------------------------------------------- helpers

    def request(self, path, data=None, headers=None):
        url = path if path.startswith("http") else self.base + path
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, headers={"User-Agent": "stockflow-site-check/1.0", **(headers or {})})
        started = time.perf_counter()
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                content = response.read()
                return response.status, response.geturl(), response.headers, content, time.perf_counter() - started
        except urllib.error.HTTPError as error:
            return error.code, url, error.headers, error.read(), time.perf_counter() - started
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            reason = getattr(error, "reason", error)
            raise SiteUnreachable(f"{url}: {reason}") from None

    def cookie(self, name):
        return next((c.value for c in self.jar if c.name == name), "")

    def record(self, ok, name, detail=""):
        self.results.append(("PASS" if ok is True else "SKIP" if ok is None else "FAIL", name, detail))
        colour = GREEN if ok is True else YELLOW if ok is None else RED
        label = "PASS" if ok is True else "SKIP" if ok is None else "FAIL"
        print(f"  {colour}{label}{RESET}  {name} {DIM}{detail}{RESET}")

    def page(self, path, *, expect_type="text/html", contains=None, allow_forbidden=True, headers=None):
        status, final_url, response_headers, content, seconds = self.request(path, headers=headers)
        kind = (response_headers.get("Content-Type") or "").split(";")[0]
        detail = f"{status} {kind} {len(content):,} B {seconds * 1000:.0f} ms"
        if status == 403 and allow_forbidden:
            self.record(None, path, "403: this role has no permission (expected for some roles)")
            return None
        if "/accounts/login/" in final_url and "/accounts/login/" not in path:
            self.record(False, path, "redirected to sign-in: session lost")
            return None
        ok = status == 200 and kind == expect_type
        if ok and expect_type == "application/pdf":
            ok = content.startswith(b"%PDF")
            detail += "" if ok else " (not a valid PDF)"
        if ok and contains:
            ok = contains.encode() in content
            detail += "" if ok else f" (missing text: {contains!r})"
        self.record(ok, path, detail)
        return content if ok else None

    def json(self, path):
        content = self.page(path, expect_type="application/json")
        return json.loads(content) if content else None

    # -------------------------------------------------------------- phases

    def public(self):
        print("\nPublic pages")
        self.page("/healthz/", expect_type="text/plain", contains="ok")
        self.page("/", contains="StockFlow")
        self.page("/accounts/login/", contains="csrfmiddlewaretoken")
        self.page("/api/docs/")
        self.page("/api/schema/", expect_type="application/vnd.oai.openapi")
        status, final_url, *_ = self.request("/products/")
        self.record("/accounts/login/" in final_url, "/products/ while signed out", "must redirect to sign-in")
        status, *_ = self.request("/api/v1/products/")
        self.record(status in (401, 403), "/api/v1/products/ while signed out", f"{status} (must be 401/403)")

    def sign_in(self, username, password):
        print(f"\nSign in as {username}")
        self.request("/accounts/login/")
        token = self.cookie("csrftoken")
        status, final_url, _, content, _ = self.request(
            "/accounts/login/",
            data={"csrfmiddlewaretoken": token, "username": username, "password": password},
            headers={"Referer": self.base + "/accounts/login/"},
        )
        ok = status == 200 and "/accounts/login/" not in final_url and b"Sign out" in content
        reason = "" if ok else ("locked out (429)" if status == 429 else f"{status}, still on sign-in page: wrong username/password?")
        self.record(ok, "sign-in", reason)
        return ok

    def sign_in_as_guest(self):
        print("\nSign in as guest (one click, no password)")
        self.request("/accounts/login/")
        status, final_url, _, content, _ = self.request(
            "/accounts/guest/",
            data={"csrfmiddlewaretoken": self.cookie("csrftoken")},
            headers={"Referer": self.base + "/"},
        )
        ok = status == 200 and "/accounts/login/" not in final_url and b"Guest mode" in content
        self.record(ok, "guest sign-in", "" if ok else "guest access is off or the guest account is missing (run bootstrap_demo)")
        return ok

    def guest_is_read_only(self):
        print("\nGuest must not be able to change anything")
        token = self.cookie("csrftoken")
        for path, data in [
            ("/products/new/", {"name": "Guest", "sku": "GUEST-CHECK", "unit_price": "1", "reorder_level": "1"}),
            ("/customers/new/", {"name": "Guest", "code": "GUEST-CHECK"}),
            ("/purchase-orders/new/", {"reference": "GUEST-CHECK"}),
        ]:
            status, *_ = self.request(
                path,
                data={"csrfmiddlewaretoken": token, **data},
                headers={"Referer": self.base + path},
            )
            self.record(status == 403, f"POST {path}", f"{status} (must be 403)")

    def workspace(self):
        print("\nWorkspace pages")
        for path in [
            "/", "/products/", "/products/new/", "/stock/", "/stock/?health=low", "/movements/",
            "/purchase-orders/?status=all", "/purchase-orders/new/", "/sales/", "/sales/new/",
            "/suppliers/", "/suppliers/new/", "/customers/", "/customers/new/",
            "/reports/", "/reports/movements/", "/activity/", "/accounts/profile/",
            "/accounts/users/", "/accounts/users/new/", "/alerts/",
        ]:
            self.page(path)

        print("\nLive search (HTMX partials)")
        for path in ["/products/?q=a", "/stock/?q=a", "/movements/?q=a", "/suppliers/?q=a", "/customers/?q=a", "/sales/?q=a"]:
            content = self.page(path, headers={"HX-Request": "true"})
            if content is not None:
                self.record(b"<html" not in content.lower(), f"{path} partial", "must return rows only, not a full page")

        print("\nDashboard data and exports")
        data = self.json("/dashboard/data/")
        if data is not None:
            self.record(bool(data), "dashboard chart data", ", ".join(sorted(data)) or "empty (no catalog permission?)")
        self.page("/reports/?format=csv", expect_type="text/csv")
        self.page("/reports/movements/?format=csv", expect_type="text/csv")
        self.page("/reports/?format=pdf", expect_type="application/pdf")

    def details(self):
        print("\nDetail pages, PDFs and API")
        summary = self.json("/api/v1/summary/")
        if summary is not None:
            self.record("stock" in summary, "API summary", json.dumps(summary.get("stock", {})))
        listings = {
            "products": "/api/v1/products/",
            "suppliers": "/api/v1/suppliers/",
            "customers": "/api/v1/customers/",
            "movements": "/api/v1/stock-movements/",
            "purchase_orders": "/api/v1/purchase-orders/",
            "sales_orders": "/api/v1/sales-orders/",
        }
        first = {}
        for name, path in listings.items():
            payload = self.json(path)
            rows = (payload or {}).get("results") or []
            first[name] = rows[0]["id"] if rows else None
            if payload is not None and not rows:
                self.record(None, f"{name} detail pages", "no records to open")

        pages = []
        if first["products"]:
            pages += [f"/products/{first['products']}/edit/", f"/products/{first['products']}/stocktake/"]
        if first["suppliers"]:
            pages.append(f"/suppliers/{first['suppliers']}/")
        if first["customers"]:
            pages.append(f"/customers/{first['customers']}/")
        if first["purchase_orders"]:
            pages.append(f"/purchase-orders/{first['purchase_orders']}/")
        if first["sales_orders"]:
            pages.append(f"/sales/{first['sales_orders']}/")
        for path in pages:
            self.page(path)
        if first["purchase_orders"]:
            self.page(f"/purchase-orders/{first['purchase_orders']}/pdf/", expect_type="application/pdf")
        if first["sales_orders"]:
            self.page(f"/sales/{first['sales_orders']}/invoice/", expect_type="application/pdf")

    def static_files(self):
        print("\nStatic files (CSS / JS)")
        _, _, _, content, _ = self.request("/")
        assets = sorted(set(re.findall(rb'(?:href|src)="(/static/[^"]+)"', content)))
        for asset in assets:
            path = asset.decode()
            status, _, headers, body, _ = self.request(path)
            self.record(status == 200 and len(body) > 0, path, f"{status} {len(body):,} B")
        if not assets:
            self.record(False, "static files", "no /static/ links found on the dashboard")

    def sign_out(self):
        token = self.cookie("csrftoken")
        status, final_url, *_ = self.request(
            "/accounts/logout/",
            data={"csrfmiddlewaretoken": token},
            headers={"Referer": self.base + "/"},
        )
        self.record("/accounts/login/" in final_url, "sign-out", f"{status}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.environ.get("CHECK_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("-u", "--username", default=os.environ.get("CHECK_USERNAME", ""))
    parser.add_argument("-p", "--password", default=os.environ.get("CHECK_PASSWORD", ""))
    parser.add_argument("--guest", action="store_true", help="check as the read-only guest (no password)")
    parser.add_argument("--timeout", type=float, default=90, help="seconds; Render free tier needs ~60 s to wake up")
    args = parser.parse_args()

    if os.name == "nt":
        os.system("")  # enable colour codes in the Windows console

    checker = Checker(args.base, args.timeout)
    print(f"Checking {checker.base}")
    try:
        checker.request("/healthz/")
    except SiteUnreachable as error:
        print()
        print(f"{RED}Cannot reach the site.{RESET} {error}")
        print("Is the server running? Start it in another terminal with:")
        print("    python manage.py runserver 127.0.0.1:8001")
        print("and check that --base matches its address and port.")
        sys.exit(2)
    checker.public()

    if args.guest:
        if checker.sign_in_as_guest():
            checker.guest_is_read_only()
            checker.workspace()
            checker.details()
            checker.static_files()
            checker.sign_out()
    elif args.username:
        password = args.password or getpass.getpass(f"Password for {args.username}: ")
        if checker.sign_in(args.username, password):
            checker.workspace()
            checker.details()
            checker.static_files()
            checker.sign_out()
    else:
        print(f"\n{YELLOW}No --username or --guest given: only public pages were checked.{RESET}")

    counts = {label: sum(1 for r in checker.results if r[0] == label) for label in ("PASS", "SKIP", "FAIL")}
    print(f"\n{GREEN}{counts['PASS']} passed{RESET}, {YELLOW}{counts['SKIP']} skipped{RESET}, {RED}{counts['FAIL']} failed{RESET}")
    for status, name, detail in checker.results:
        if status == "FAIL":
            print(f"  {RED}FAIL{RESET} {name} {detail}")
    sys.exit(1 if counts["FAIL"] else 0)


if __name__ == "__main__":
    main()
