from unittest.mock import patch

from django.test import SimpleTestCase

from .health import _check_cache


class LivenessTests(SimpleTestCase):
    def test_always_200(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class ReadinessTests(SimpleTestCase):
    """DB and cache checks are mocked independently -- deterministic
    coverage of all four combinations, regardless of whether a real Redis
    happens to be running wherever this suite executes.
    """

    def test_both_healthy(self):
        with patch("config.health._check_database", return_value=True), \
             patch("config.health._check_cache", return_value=True):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"database": "ok", "cache": "ok", "status": "ok"})

    def test_database_failure_is_unhealthy_regardless_of_cache(self):
        with patch("config.health._check_database", return_value=False), \
             patch("config.health._check_cache", return_value=True):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload["status"], "unhealthy")
        self.assertEqual(payload["database"], "error")

    def test_cache_unreachable_alone_is_degraded_not_unhealthy(self):
        """The core invariant of this milestone's review: a Redis outage
        alone must never take a replica out of an LB's rotation.
        """
        with patch("config.health._check_database", return_value=True), \
             patch("config.health._check_cache", return_value=False):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["database"], "ok")
        self.assertEqual(payload["cache"], "unavailable")

    def test_cache_not_configured_is_still_ok(self):
        with patch("config.health._check_database", return_value=True), \
             patch("config.health._check_cache", return_value=None):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["cache"], "not configured")


class CheckCacheTests(SimpleTestCase):
    def test_returns_none_when_django_cache_url_is_unset(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("DJANGO_CACHE_URL", None)
            self.assertIsNone(_check_cache())

    def test_returns_false_on_an_unreachable_redis(self):
        with patch.dict("os.environ", {"DJANGO_CACHE_URL": "redis://127.0.0.1:1/0"}):
            self.assertFalse(_check_cache())
