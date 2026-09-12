import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from django.test import SimpleTestCase


class ProductionSettingsTests(SimpleTestCase):
    def load_settings(self, overrides, expr="s.DEBUG"):
        env = {key: value for key, value in os.environ.items() if key not in (
            "DJANGO_ENV", "DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "FIELD_ENCRYPTION_KEY", "PUBLIC_BASE_URL",
            "DB_ENGINE", "BEHIND_REVERSE_PROXY", "DJANGO_CACHE_URL", "HSTS_SECONDS", "HSTS_INCLUDE_SUBDOMAINS",
            "HSTS_PRELOAD", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST")}
        env.update(overrides)
        return subprocess.run([sys.executable, "-c", f"import config.settings as s; print({expr})"],
            cwd=Path(__file__).resolve().parent.parent, env=env, capture_output=True, text=True, timeout=15)

    def valid(self):
        return {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": "x" * 50,
                "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(), "PUBLIC_BASE_URL": "https://school.example",
                "DJANGO_ALLOWED_HOSTS": "school.example", "DB_ENGINE": "postgres",
                "DJANGO_CACHE_URL": "redis://localhost:6379/2",
                "POSTGRES_DB": "school_management", "POSTGRES_USER": "school_management",
                "POSTGRES_PASSWORD": "rc-test-password", "POSTGRES_HOST": "postgres"}

    def test_production_refuses_missing_configuration(self):
        self.assertNotEqual(self.load_settings({"DJANGO_ENV": "production"}).returncode, 0)

    def test_explicit_production_configuration_disables_debug(self):
        result = self.load_settings(self.valid())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")

    def test_production_refuses_local_callback_url_and_sqlite(self):
        for overrides in ({"PUBLIC_BASE_URL": "http://localhost:8000"}, {"DB_ENGINE": "sqlite"}, {"FIELD_ENCRYPTION_KEY": "invalid"}):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.load_settings({**self.valid(), **overrides}).returncode, 0)

    def test_production_refuses_missing_cache_url(self):
        no_cache_url = {key: value for key, value in self.valid().items() if key != "DJANGO_CACHE_URL"}
        self.assertNotEqual(self.load_settings(no_cache_url).returncode, 0)

    def test_production_refuses_missing_or_blank_postgres_configuration(self):
        for name in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST"):
            for value in (None, "   "):
                with self.subTest(name=name, value=value):
                    overrides = self.valid()
                    if value is None:
                        overrides.pop(name)
                    else:
                        overrides[name] = value
                    self.assertNotEqual(self.load_settings(overrides).returncode, 0)

    def test_production_refuses_development_postgres_password(self):
        self.assertNotEqual(
            self.load_settings({**self.valid(), "POSTGRES_PASSWORD": "school_management_dev"}).returncode,
            0,
        )

    def test_production_refuses_invalid_cache_urls(self):
        for cache_url in ("garbage://whatever", "redis:///2", "not-a-url"):
            with self.subTest(cache_url=cache_url):
                self.assertNotEqual(
                    self.load_settings({**self.valid(), "DJANGO_CACHE_URL": cache_url}).returncode,
                    0,
                )

    def test_production_enables_security_headers(self):
        checks = {
            "s.SECURE_SSL_REDIRECT": "True",
            "s.SECURE_HSTS_SECONDS": "31536000",
            "s.SECURE_HSTS_INCLUDE_SUBDOMAINS": "True",
            "s.SECURE_HSTS_PRELOAD": "True",
            "s.SECURE_CONTENT_TYPE_NOSNIFF": "True",
            "s.X_FRAME_OPTIONS": "DENY",
            "s.SECURE_REFERRER_POLICY": "strict-origin-when-cross-origin",
        }
        for expr, expected in checks.items():
            with self.subTest(expr=expr):
                result = self.load_settings(self.valid(), expr=expr)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_hsts_posture_is_independently_env_overridable(self):
        overrides = {
            "HSTS_SECONDS": "3600",
            "HSTS_INCLUDE_SUBDOMAINS": "false",
            "HSTS_PRELOAD": "false",
        }
        result = self.load_settings({**self.valid(), **overrides}, expr="(s.SECURE_HSTS_SECONDS, s.SECURE_HSTS_INCLUDE_SUBDOMAINS, s.SECURE_HSTS_PRELOAD)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "(3600, False, False)")

    def test_proxy_ssl_header_defaults_on_and_is_reversible_via_env(self):
        default_result = self.load_settings(self.valid(), expr="s.SECURE_PROXY_SSL_HEADER")
        self.assertEqual(default_result.returncode, 0, default_result.stderr)
        self.assertEqual(default_result.stdout.strip(), "('HTTP_X_FORWARDED_PROTO', 'https')")

        disabled_result = self.load_settings(
            {**self.valid(), "BEHIND_REVERSE_PROXY": "false"},
            expr="getattr(s, 'SECURE_PROXY_SSL_HEADER', None)",
        )
        self.assertEqual(disabled_result.returncode, 0, disabled_result.stderr)
        self.assertEqual(disabled_result.stdout.strip(), "None")

    def test_production_uses_an_explicit_fail_open_redis_cache_not_locmem(self):
        result = self.load_settings(
            self.valid(), expr="(s.CACHES['default']['BACKEND'], s.CACHES['default']['LOCATION'])",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), "('config.cache.FailOpenRedisCache', 'redis://localhost:6379/2')",
        )

    def test_development_keeps_the_default_local_cache(self):
        result = self.load_settings({}, expr="'CACHES' in dir(s)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")

    def test_non_production_with_cache_url_still_gets_the_shared_redis_cache(self):
        """RC Area 6 defect found live: this used to be gated behind
        `if PRODUCTION`, so a non-production environment that still runs
        multiple worker processes (RC/load-test's gunicorn --workers 4,
        docker-compose.loadtest.yml) silently fell back to Django's default
        per-process cache -- multiplying every DRF throttle ceiling by
        however many workers happened to receive a given request, instead of
        sharing counters. A 5E-2 run measured this directly: 0% errors at
        any ramp step despite far exceeding the per-user throttle."""
        result = self.load_settings(
            {"DJANGO_CACHE_URL": "redis://localhost:6379/3"},
            expr="(s.CACHES['default']['BACKEND'], s.CACHES['default']['LOCATION'])",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), "('config.cache.FailOpenRedisCache', 'redis://localhost:6379/3')",
        )

    def test_non_production_refuses_invalid_cache_urls_too(self):
        for cache_url in ("garbage://whatever", "redis:///2", "not-a-url"):
            with self.subTest(cache_url=cache_url):
                self.assertNotEqual(self.load_settings({"DJANGO_CACHE_URL": cache_url}).returncode, 0)

    def test_postgres_backend_reuses_connections_with_health_checks(self):
        result = self.load_settings(
            {"DB_ENGINE": "postgres"},
            expr="(s.DATABASES['default']['CONN_MAX_AGE'], s.DATABASES['default']['CONN_HEALTH_CHECKS'])",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "(60, True)")
