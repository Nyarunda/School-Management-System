import datetime

from django.db import models, transaction
from django.utils import timezone

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 60  # 1m, 2m, 4m, 8m, 16m...
DEFAULT_LEASE_SECONDS = 300


class DurableWorkStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    PROCESSED = "PROCESSED", "Processed"
    FAILED = "FAILED", "Failed"


class DurableWorkModel(models.Model):
    """Abstract mixin for anything a Celery consumer processes with atomic
    claim/lease/backoff semantics. `available_at` (backoff deadline, meaningful
    while PENDING) and `lease_expires_at` (processing lease, meaningful while
    PROCESSING) are deliberately separate fields -- overloading one field for
    both meanings is exactly the kind of ambiguity that caused a bug
    elsewhere in this project. Concrete subclasses live in whichever app
    owns the underlying business event (e.g. apps.notifications.NotificationOutbox).
    """

    status = models.CharField(max_length=20, choices=DurableWorkStatus.choices, default=DurableWorkStatus.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    available_at = models.DateTimeField(default=timezone.now)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def mark_processing(self, lease_seconds=DEFAULT_LEASE_SECONDS):
        self.status = DurableWorkStatus.PROCESSING
        self.attempts += 1
        self.lease_expires_at = timezone.now() + datetime.timedelta(seconds=lease_seconds)
        self.save(update_fields=["status", "attempts", "lease_expires_at"])

    def mark_processed(self):
        self.status = DurableWorkStatus.PROCESSED
        self.processed_at = timezone.now()
        self.lease_expires_at = None
        self.save(update_fields=["status", "processed_at", "lease_expires_at"])

    def mark_failed(self, error):
        self.last_error = str(error)[:2000]
        self.lease_expires_at = None
        if self.attempts >= MAX_ATTEMPTS:
            self.status = DurableWorkStatus.FAILED  # dead-letter: no further automatic retries
        else:
            self.status = DurableWorkStatus.PENDING
            # attempts was already incremented by mark_processing() for the
            # attempt that just failed, so attempt 1's failure backs off by
            # BASE * 2^0, not 2^1 -- attempts - 1 is deliberate, not a typo.
            self.available_at = timezone.now() + datetime.timedelta(seconds=BACKOFF_BASE_SECONDS * (2 ** (self.attempts - 1)))
        self.save(update_fields=["status", "last_error", "available_at", "lease_expires_at"])


def claim_due(queryset, *, limit=100, lease_seconds=DEFAULT_LEASE_SECONDS):
    """Atomically claim up to `limit` PENDING-and-due rows, marking each
    PROCESSING before returning them -- select_for_update(skip_locked=True)
    is the same "claim exclusively, don't wait on a concurrent claimant's
    lock" idiom already used throughout apps/finance/services.py, applied
    here so two overlapping Beat/worker runs can never both process the
    same row. skip_locked is PostgreSQL-only; SQLite (dev/test default)
    silently ignores select_for_update() entirely, which is fine there
    since automated tests call task bodies directly/single-threaded.
    """
    with transaction.atomic():
        rows = list(
            queryset.filter(status=DurableWorkStatus.PENDING, available_at__lte=timezone.now())
            .select_for_update(skip_locked=True)
            .order_by("available_at", "pk")[:limit]
        )
        for row in rows:
            row.mark_processing(lease_seconds=lease_seconds)
    return rows


def reap_stale(queryset):
    """Reclaim PROCESSING rows whose lease expired (a crashed/killed worker
    never finished) back to PENDING. Locked the same way as claim_due so a
    worker that's genuinely mid-save can't have its row yanked back out
    from under it mid-flight.
    """
    reclaimed = 0
    with transaction.atomic():
        for row in queryset.filter(status=DurableWorkStatus.PROCESSING, lease_expires_at__lt=timezone.now()).select_for_update(skip_locked=True):
            row.status = DurableWorkStatus.PENDING
            row.available_at = timezone.now()
            row.lease_expires_at = None
            if not row.last_error:
                row.last_error = "Processing lease expired before completion"
            row.save(update_fields=["status", "available_at", "lease_expires_at", "last_error"])
            reclaimed += 1
    return reclaimed
