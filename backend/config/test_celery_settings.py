from django.conf import settings
from django.test import SimpleTestCase


class CelerySettingsTests(SimpleTestCase):
    def test_acks_late_and_reject_on_worker_lost_are_enabled(self):
        self.assertTrue(settings.CELERY_TASK_ACKS_LATE)
        self.assertTrue(settings.CELERY_TASK_REJECT_ON_WORKER_LOST)

    def test_time_limits_are_positive_and_soft_precedes_hard(self):
        self.assertGreater(settings.CELERY_TASK_TIME_LIMIT, 0)
        self.assertGreater(settings.CELERY_TASK_SOFT_TIME_LIMIT, 0)
        self.assertLess(settings.CELERY_TASK_SOFT_TIME_LIMIT, settings.CELERY_TASK_TIME_LIMIT)

    def test_worker_prefetch_multiplier_is_one(self):
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1)

    def test_broker_connection_retry_on_startup_is_enabled(self):
        self.assertTrue(settings.CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP)
