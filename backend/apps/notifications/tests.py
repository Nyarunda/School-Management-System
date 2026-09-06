from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.activity.durable_work import DurableWorkStatus
from apps.tenancy.models import Tenant

from .models import NotificationChannel, NotificationOutbox
from .services import enqueue_notification
from .tasks import dispatch_pending_notifications, reap_stale_notifications


class EnqueueNotificationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

    def test_enqueue_is_idempotent_on_the_same_key(self):
        first = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )
        second = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(NotificationOutbox.objects.count(), 1)

    def test_enqueue_creates_distinct_rows_for_distinct_keys(self):
        enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )
        enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:2:1",
        )
        self.assertEqual(NotificationOutbox.objects.count(), 2)


class DispatchPendingNotificationsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

    def test_dispatch_marks_a_pending_row_processed(self):
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )
        dispatch_pending_notifications()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.PROCESSED)
        self.assertIsNotNone(outbox.processed_at)

    def test_forced_failure_retries_then_dead_letters(self):
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )

        def boom(*args, **kwargs):
            raise RuntimeError("provider unreachable")

        import apps.notifications.tasks as tasks_module
        original_logger_info = tasks_module.logger.info
        tasks_module.logger.info = boom
        try:
            for _ in range(5):
                outbox.refresh_from_db()
                outbox.available_at = timezone.now() - timedelta(seconds=1)
                outbox.save(update_fields=["available_at"])
                dispatch_pending_notifications()
        finally:
            tasks_module.logger.info = original_logger_info

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.FAILED)
        self.assertIn("provider unreachable", outbox.last_error)


class ReapStaleNotificationsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

    def test_reap_stale_reclaims_an_expired_processing_row(self):
        outbox = NotificationOutbox.objects.create(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
            status=DurableWorkStatus.PROCESSING, lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        reap_stale_notifications()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.PENDING)
