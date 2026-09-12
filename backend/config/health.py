import os

from django.db import connections
from django.http import JsonResponse


def liveness(request):
    """Proves the process can respond at all -- no dependency checks."""
    return JsonResponse({"status": "ok"})


def _check_database():
    try:
        connections["default"].cursor().execute("SELECT 1")
        return True
    except Exception:
        return False


def _check_cache():
    """Checks DJANGO_CACHE_URL directly -- the dependency actually used in
    the synchronous HTTP request path (via throttling) -- not
    CELERY_BROKER_URL, which is a separate dependency only used by
    out-of-band workers even though both happen to point at the same Redis
    today. Deliberately bypasses config.cache.FailOpenRedisCache: that
    wrapper's whole job is to hide a Redis outage from request-serving
    code, which would defeat a readiness probe's purpose of surfacing it.
    Returns None when DJANGO_CACHE_URL isn't set (dev/test, where CACHES
    isn't Redis-backed at all) -- not applicable there, not a failure.
    """
    cache_url = os.getenv("DJANGO_CACHE_URL")
    if not cache_url:
        return None
    try:
        import redis
        client = redis.from_url(cache_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return True
    except Exception:
        return False


def readiness(request):
    """PostgreSQL is a hard dependency for serving any request; Redis/cache
    is deliberately soft, matching config.cache.FailOpenRedisCache's own
    fail-open design -- a Redis outage alone must never take every replica
    out of a load balancer's rotation, only report as degraded.
    """
    database_ok = _check_database()
    cache_result = _check_cache()
    payload = {"database": "ok" if database_ok else "error"}
    if cache_result is None:
        payload["cache"] = "not configured"
    else:
        payload["cache"] = "ok" if cache_result else "unavailable"

    if not database_ok:
        payload["status"] = "unhealthy"
        return JsonResponse(payload, status=503)
    payload["status"] = "ok" if cache_result in (True, None) else "degraded"
    return JsonResponse(payload, status=200)
