import logging

from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)


class FailOpenRedisCache(RedisCache):
    """Same as Django's built-in RedisCache, except a backend failure (Redis
    unreachable, connection refused, timeout, ...) is logged and swallowed
    rather than propagated.

    Django's built-in Redis backend has no IGNORE_EXCEPTIONS-style option
    (unlike its memcached backends -- OPTIONS is passed straight through as
    connection-pool kwargs to the redis client, so an unrecognized key there
    raises a TypeError, it doesn't configure failure behavior). Without this
    wrapper, a Redis outage would turn into a 500 for every authenticated
    request (DRF throttles call cache.get()/set() on every request) or,
    worse, could be the reason a legitimate M-Pesa webhook callback never
    even gets as far as being logged. Read failures degrade to a cache miss
    (the `default` passed in); write failures are no-ops. Redis HA/monitoring
    itself is a separate, later hardening concern -- this only prevents a
    cache outage from becoming a request outage.
    """

    def _safe(self, method_name, default, *args, **kwargs):
        try:
            return getattr(super(), method_name)(*args, **kwargs)
        except Exception:
            logger.exception("Cache backend error on %s(); failing open", method_name)
            return default

    def add(self, key, value, *args, **kwargs):
        return self._safe("add", False, key, value, *args, **kwargs)

    def get(self, key, default=None, *args, **kwargs):
        return self._safe("get", default, key, default, *args, **kwargs)

    def set(self, key, value, *args, **kwargs):
        return self._safe("set", None, key, value, *args, **kwargs)

    def touch(self, key, *args, **kwargs):
        return self._safe("touch", False, key, *args, **kwargs)

    def delete(self, key, *args, **kwargs):
        return self._safe("delete", False, key, *args, **kwargs)

    def get_many(self, keys, *args, **kwargs):
        return self._safe("get_many", {}, keys, *args, **kwargs)

    def has_key(self, key, *args, **kwargs):
        return self._safe("has_key", False, key, *args, **kwargs)

    def incr(self, key, delta=1, *args, **kwargs):
        return self._safe("incr", None, key, delta, *args, **kwargs)

    def set_many(self, data, *args, **kwargs):
        return self._safe("set_many", list(data.keys()), data, *args, **kwargs)

    def delete_many(self, keys, *args, **kwargs):
        return self._safe("delete_many", None, keys, *args, **kwargs)

    def clear(self):
        return self._safe("clear", None)
