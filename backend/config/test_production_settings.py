import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from django.test import SimpleTestCase


class ProductionSettingsTests(SimpleTestCase):
    def load_settings(self, overrides):
        env = {key: value for key, value in os.environ.items() if key not in (
            "DJANGO_ENV", "DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "FIELD_ENCRYPTION_KEY", "PUBLIC_BASE_URL", "DB_ENGINE")}
        env.update(overrides)
        return subprocess.run([sys.executable, "-c", "import config.settings as s; print(s.DEBUG)"],
            cwd=Path(__file__).resolve().parent.parent, env=env, capture_output=True, text=True, timeout=15)

    def valid(self):
        return {"DJANGO_ENV": "production", "DJANGO_SECRET_KEY": "x" * 50,
                "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(), "PUBLIC_BASE_URL": "https://school.example",
                "DJANGO_ALLOWED_HOSTS": "school.example", "DB_ENGINE": "postgres"}

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
