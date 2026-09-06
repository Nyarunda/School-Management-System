import logging

from celery import shared_task

from apps.activity.durable_work import claim_due, reap_stale

from .models import NotificationOutbox

logger = logging.getLogger(__name__)


@shared_task
def dispatch_pending_notifications():
    for outbox in claim_due(NotificationOutbox.objects.all(), limit=200):
        try:
            # Stubbed: proves the outbox -> retry -> dead-letter pipeline
            # end-to-end without a real SMS/email provider (its own future
            # milestone, mirroring M-Pesa's).
            logger.info("NOTIFICATION (stub): %s to %s via %s -- %s", outbox.message_type, outbox.recipient, outbox.channel, outbox.context)
        except Exception as error:
            outbox.mark_failed(error)
            continue
        outbox.mark_processed()


@shared_task
def reap_stale_notifications():
    reap_stale(NotificationOutbox.objects.all())
