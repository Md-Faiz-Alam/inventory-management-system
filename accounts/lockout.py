"""Failed sign-in counting shared by the login page and the API token endpoint."""

from django.conf import settings
from django.core.cache import cache

from audit.log import client_ip


def _limit():
    return getattr(settings, "LOGIN_ATTEMPT_LIMIT", 5)


def _seconds():
    return getattr(settings, "LOGIN_LOCK_SECONDS", 15 * 60)


def attempt_key(request, username):
    return f"login-attempts:{(username or '').strip().lower()}:{client_ip(request)}"


def is_locked(request, username):
    return cache.get(attempt_key(request, username), 0) >= _limit()


def record_failure(request, username):
    key = attempt_key(request, username)
    # add() only creates the key, so the lock window starts at the first failure.
    if not cache.add(key, 1, _seconds()):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, _seconds())


def clear(request, username):
    cache.delete(attempt_key(request, username))
