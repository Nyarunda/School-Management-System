from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.notifications.models import NotificationChannel, NotificationOutbox
from apps.tenancy.models import Tenant

from .durable_work import BACKOFF_BASE_SECONDS, MAX_ATTEMPTS, DurableWorkStatus, claim_due, reap_stale


def make_outbox(tenant, **kwargs):
    defaults = {
        "tenant": tenant, "channel": NotificationChannel.SMS, "recipient": "0712345678",
        "message_type": "test", "idempotency_key": f"test-{NotificationOutbox.objects.count()}",
    }
    defaults.update(kwargs)
    return NotificationOutbox.objects.create(**defaults)


class DurableWorkLifecycleTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

    def test_mark_processing_increments_attempts_and_sets_lease(self):
        row = make_outbox(self.tenant)
        row.mark_processing(lease_seconds=120)
        row.refresh_from_db()
        self.assertEqual(row.status, DurableWorkStatus.PROCESSING)
        self.assertEqual(row.attempts, 1)
        self.assertIsNotNone(row.lease_expires_at)

    def test_mark_processed_clears_lease_and_sets_timestamp(self):
        row = make_outbox(self.tenant)
        row.mark_processing()
        row.mark_processed()
        row.refresh_from_db()
        self.assertEqual(row.status, DurableWorkStatus.PROCESSED)
        self.assertIsNone(row.lease_expires_at)
        self.assertIsNotNone(row.processed_at)

    def test_mark_failed_backs_off_with_exact_exponential_sequence(self):
        row = make_outbox(self.tenant)
        expected_delays = [BACKOFF_BASE_SECONDS * (2 ** exponent) for exponent in range(MAX_ATTEMPTS - 1)]
        for expected_delay in expected_delays:
            before = timezone.now()
            row.mark_processing()
            row.mark_failed("boom")
            row.refresh_from_db()
            self.assertEqual(row.status, DurableWorkStatus.PENDING)
            self.assertAlmostEqual(
                (row.available_at - before).total_seconds(), expected_delay, delta=5,
            )

    def test_mark_failed_dead_letters_after_max_attempts(self):
        row = make_outbox(self.tenant)
        for _ in range(MAX_ATTEMPTS):
            row.mark_processing()
            row.mark_failed("boom")
            row.refresh_from_db()
        self.assertEqual(row.status, DurableWorkStatus.FAILED)
        self.assertEqual(row.attempts, MAX_ATTEMPTS)

    def test_claim_due_excludes_not_yet_due_and_already_processing_rows(self):
        due = make_outbox(self.tenant)
        not_yet_due = make_outbox(self.tenant, available_at=timezone.now() + timedelta(hours=1))
        already_processing = make_outbox(self.tenant, status=DurableWorkStatus.PROCESSING)

        claimed = claim_due(NotificationOutbox.objects.filter(tenant=self.tenant))

        self.assertEqual([row.pk for row in claimed], [due.pk])
        not_yet_due.refresh_from_db()
        already_processing.refresh_from_db()
        self.assertEqual(not_yet_due.status, DurableWorkStatus.PENDING)
        self.assertEqual(already_processing.status, DurableWorkStatus.PROCESSING)

    def test_reap_stale_reclaims_expired_lease_and_leaves_fresh_lease_alone(self):
        expired = make_outbox(
            self.tenant, status=DurableWorkStatus.PROCESSING,
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        fresh = make_outbox(
            self.tenant, status=DurableWorkStatus.PROCESSING,
            lease_expires_at=timezone.now() + timedelta(hours=1),
        )

        reclaimed = reap_stale(NotificationOutbox.objects.filter(tenant=self.tenant))

        self.assertEqual(reclaimed, 1)
        expired.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(expired.status, DurableWorkStatus.PENDING)
        self.assertTrue(expired.last_error)
        self.assertEqual(fresh.status, DurableWorkStatus.PROCESSING)

    def test_reap_stale_only_appends_default_error_when_none_present(self):
        expired = make_outbox(
            self.tenant, status=DurableWorkStatus.PROCESSING,
            lease_expires_at=timezone.now() - timedelta(seconds=1), last_error="custom failure",
        )

        reap_stale(NotificationOutbox.objects.filter(tenant=self.tenant))

        expired.refresh_from_db()
        self.assertEqual(expired.last_error, "custom failure")

    def test_reap_stale_is_bounded_by_limit(self):
        expired_rows = [
            make_outbox(self.tenant, status=DurableWorkStatus.PROCESSING, lease_expires_at=timezone.now() - timedelta(seconds=1))
            for _ in range(3)
        ]

        reclaimed = reap_stale(NotificationOutbox.objects.filter(tenant=self.tenant), limit=2)

        self.assertEqual(reclaimed, 2)
        statuses = [row.status for row in NotificationOutbox.objects.filter(pk__in=[row.pk for row in expired_rows])]
        self.assertEqual(statuses.count(DurableWorkStatus.PENDING), 2)
        self.assertEqual(statuses.count(DurableWorkStatus.PROCESSING), 1)

    def test_a_task_interrupted_mid_processing_is_reclaimed_and_re_claimable(self):
        """Milestone 22.3: backs the CELERY_TASK_SOFT_TIME_LIMIT documentation
        in config/settings.py -- a task killed mid-processing (a timeout, a
        worker crash) never calls mark_processed/mark_failed, exactly like
        this test's interrupted claim. Proves the existing lease mechanism
        alone is what makes that safe: the row isn't just flipped back to
        PENDING, it's fully re-claimable by the next run.
        """
        row = make_outbox(self.tenant)
        queryset = NotificationOutbox.objects.filter(tenant=self.tenant)

        claimed = claim_due(queryset, lease_seconds=1)
        self.assertEqual([claimed_row.pk for claimed_row in claimed], [row.pk])
        # Simulate the interruption: no mark_processed()/mark_failed() call.
        # Force the lease into the past instead of sleeping past it.
        NotificationOutbox.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))

        reclaimed = reap_stale(queryset)
        self.assertEqual(reclaimed, 1)
        row.refresh_from_db()
        self.assertEqual(row.status, DurableWorkStatus.PENDING)

        re_claimed = claim_due(queryset)
        self.assertEqual([re_claimed_row.pk for re_claimed_row in re_claimed], [row.pk])

    def test_claim_due_returns_rows_with_attempts_already_incremented(self):
        row = make_outbox(self.tenant)

        claimed = claim_due(NotificationOutbox.objects.filter(tenant=self.tenant))

        self.assertEqual(len(claimed), 1)
        claimed_row = claimed[0]
        self.assertEqual(claimed_row.attempts, 1)
        # A caller's subsequent mark_failed() must compute backoff from this
        # already-incremented value, not double-count the increment.
        before = timezone.now()
        claimed_row.mark_failed("boom")
        claimed_row.refresh_from_db()
        self.assertAlmostEqual((claimed_row.available_at - before).total_seconds(), BACKOFF_BASE_SECONDS, delta=5)
