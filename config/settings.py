"""Settings for local development (PostgreSQL via Docker) and production (Render + Neon)."""

import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def required_environment(name: str) -> str:
    """Read a required environment variable without exposing its value."""
    value = os.environ.get(name, "").strip()

    if not value:
        raise ImproperlyConfigured(
            f"{name} must be configured in the environment or .env file."
        )

    return value


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = required_environment("DJANGO_SECRET_KEY")

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

# The test client speaks plain HTTP and runs before collectstatic in CI.
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Render sets this automatically for every web service.
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "accounts.apps.AccountsConfig",
    "inventory.apps.InventoryConfig",
    "suppliers.apps.SuppliersConfig",
    "reports.apps.ReportsConfig",
    "sales.apps.SalesConfig",
    "audit.apps.AuditConfig",
    "api.apps.ApiConfig",
]

LOGIN_URL = "login"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Production: DATABASE_URL (Neon). Local: the POSTGRES_* variables used with compose.yaml.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "60")),
            conn_health_checks=True,
            ssl_require=os.environ.get("DB_SSL_REQUIRE", "true").lower() == "true",
        )
    }
    DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 10
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": required_environment("POSTGRES_DB"),
            "USER": required_environment("POSTGRES_USER"),
            "PASSWORD": required_environment("POSTGRES_PASSWORD"),
            "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.environ.get("POSTGRES_PORT", "5433"),
            "CONN_MAX_AGE": 0,
            "OPTIONS": {
                "connect_timeout": 5,
            },
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or TESTING
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ------------------------------------------------------------------ security

# Render terminates TLS at its proxy and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Header holding the real client IP, set by a proxy the client cannot bypass.
# Render runs behind Cloudflare, which overwrites True-Client-IP.
CLIENT_IP_HEADER = os.environ.get(
    "CLIENT_IP_HEADER", "HTTP_TRUE_CLIENT_IP" if RENDER_EXTERNAL_HOSTNAME else ""
).strip()

SECURE_HTTPS = not TESTING and (
    os.environ.get("DJANGO_SECURE_HTTPS", "false" if DEBUG else "true").lower() == "true"
)
if SECURE_HTTPS:
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "2592000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# The app is served from a host we do not own the parent domain of (*.onrender.com),
# so HSTS includeSubDomains/preload cannot be applied meaningfully.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True

# Login lockout counters must be shared by every Gunicorn worker, so production
# keeps them in a database table (created by `createcachetable` in build.sh).
if DATABASE_URL and not TESTING:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "django_cache",
        }
    }

LOGIN_ATTEMPT_LIMIT = int(os.environ.get("LOGIN_ATTEMPT_LIMIT", "5"))
LOGIN_LOCK_SECONDS = int(os.environ.get("LOGIN_LOCK_SECONDS", "900"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING" if TESTING else "INFO"},
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "CRITICAL" if TESTING else "INFO",
            "propagate": False,
        },
    },
}

if TESTING:
    import warnings

    warnings.filterwarnings("ignore", message="No directory at", module="django.core.handlers.base")

# ------------------------------------------------------------------ REST API

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": ["rest_framework.filters.SearchFilter"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": "600/hour"},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "StockFlow Inventory API",
    "DESCRIPTION": (
        "JSON API for products, suppliers, customers, stock movements, purchase "
        "orders and sales orders. Sign in to the web app first to try requests here, "
        "or obtain a token from /api/v1/auth/token/ and send 'Authorization: Token <key>'."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SWAGGER_UI_SETTINGS": {"persistAuthorization": True},
}

# ------------------------------------------------------------------ branding & demo

COMPANY = {
    "name": os.environ.get("COMPANY_NAME", "StockFlow Demo Traders"),
    "address": os.environ.get("COMPANY_ADDRESS", "Bengaluru, Karnataka, India"),
    "gstin": os.environ.get("COMPANY_GSTIN", ""),
    "email": os.environ.get("COMPANY_EMAIL", ""),
    "phone": os.environ.get("COMPANY_PHONE", ""),
}

# When DEMO_MODE is on, the landing and login pages show the demo accounts that
# `bootstrap_demo` creates. Passwords come from the environment, never the code.
DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"

# One-click, read-only "Explore as guest" sign-in on the landing and login pages.
GUEST_ACCESS = (
    os.environ.get("GUEST_ACCESS", "").strip().lower() or ("true" if DEMO_MODE else "false")
) == "true"
GUEST_SESSION_SECONDS = int(os.environ.get("GUEST_SESSION_SECONDS", "7200"))
DEMO_LOGINS = []
if DEMO_MODE:
    for role, summary in [
        ("Admin", "Everything, including users & roles"),
        ("Manager", "Operations, purchasing, sales, corrections"),
        ("Staff", "Receive, issue, fulfil orders"),
    ]:
        username = f"demo_{role.lower()}"
        password = os.environ.get(f"DEMO_{role.upper()}_PASSWORD", "").strip()
        if password:
            DEMO_LOGINS.append(
                {"role": role, "username": username, "password": password, "summary": summary}
            )
