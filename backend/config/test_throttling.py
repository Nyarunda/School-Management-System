"""DRF throttle counters live in the configured cache -- Django's TestCase
transaction rollback does NOT reset the cache, so every test here clears it
explicitly (setUp + addCleanup) to avoid state leaking between methods.

Also: DRF's throttle classes read DEFAULT_THROTTLE_RATES into a class
attribute (SimpleRateThrottle.THROTTLE_RATES) at import time, not per-request
-- override_settings(REST_FRAMEWORK=...) does NOT change already-imported
throttle classes' effective rates. Patching THROTTLE_RATES directly on the
throttle class is the correct way to exercise a tight rate in a test.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from apps.tenancy.models import User


class ThrottlingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.user = User.objects.create_user(username="throttle-user", password="secret")
        self.client.force_authenticate(self.user)

    def test_authenticated_requests_beyond_the_user_rate_are_throttled(self):
        with patch.object(UserRateThrottle, "THROTTLE_RATES", {"user": "1/min"}):
            first = self.client.get("/api/v1/session/")
            self.assertNotEqual(first.status_code, 429)
            second = self.client.get("/api/v1/session/")
        self.assertEqual(second.status_code, 429)

    def test_a_handful_of_requests_never_trips_the_real_default_rate(self):
        for _ in range(5):
            response = self.client.get("/api/v1/session/")
            self.assertNotEqual(response.status_code, 429)

    def test_cache_backend_outage_fails_open_not_closed(self):
        """A Redis outage must never turn into every authenticated request
        failing -- config.cache.FailOpenRedisCache swallows backend errors,
        so throttling (and anything else on this cache) degrades to
        fail-open rather than a 500 for every request or, worse, a dropped
        M-Pesa webhook. Points at a port nothing listens on to force a real
        connection failure rather than mocking the client.
        """
        broken_cache = {
            "default": {
                "BACKEND": "config.cache.FailOpenRedisCache",
                "LOCATION": "redis://127.0.0.1:1/0",
            }
        }
        with override_settings(CACHES=broken_cache):
            response = self.client.get("/api/v1/session/")
        self.assertNotEqual(response.status_code, 500)
        self.assertNotEqual(response.status_code, 429)


class LoginThrottleTests(TestCase):
    """RC Area 2: LoginView/InviteAcceptView use a dedicated "login" scope,
    distinct from and much tighter than the general anon rate, since they
    are credential-verification endpoints.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        from rest_framework.test import APIClient
        self.client = APIClient()

    def test_login_is_throttled_at_the_dedicated_scope_not_the_anon_rate(self):
        with patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"login": "1/min"}):
            first = self.client.post("/api/v1/auth/login/", {"username": "nobody", "password": "wrong"}, format="json")
            self.assertNotEqual(first.status_code, 429)
            second = self.client.post("/api/v1/auth/login/", {"username": "nobody", "password": "wrong"}, format="json")
        self.assertEqual(second.status_code, 429)
