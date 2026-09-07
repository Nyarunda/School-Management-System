import logging

from celery import shared_task
from django.utils import timezone

from apps.activity.durable_work import claim_due, reap_stale

from .gateways import resolve_gateway
from .models import NotificationDeliveryAttempt, NotificationDeliveryAttemptStatus, NotificationOutbox

logger = logging.getLogger(__name__)


@shared_task
def dispatch_pending_notifications():
    """subject/body were already rendered at publish_notification_event
    time and stored in outbox.context -- dispatch never needs to re-resolve
    a NotificationRule/NotificationTemplate, so a template edited after a
    notification was queued never changes an already-queued message's
    content (the same snapshot-at-commit-time discipline used elsewhere in
    this codebase, e.g. Leave's LeaveRequestApproval).
    """
    for outbox in claim_due(NotificationOutbox.objects.all(), limit=200):
        attempt = NotificationDeliveryAttempt.objects.create(
            tenant=outbox.tenant, notification=outbox, attempt_number=outbox.attempts, provider=outbox.channel,
        )
        try:
            gateway = resolve_gateway(channel=outbox.channel)
            provider_reference = gateway.send(
                tenant=outbox.tenant, recipient=outbox.recipient,
                subject=outbox.context.get("subject", ""), body=outbox.context.get("body", ""),
                sender_id="", context=outbox.context,
            )
        except Exception as error:
            attempt.status = NotificationDeliveryAttemptStatus.FAILED
            attempt.error_message = str(error)[:2000]
            attempt.completed_at = timezone.now()
            attempt.save(update_fields=["status", "error_message", "completed_at"])
            outbox.mark_failed(error)
            continue
        attempt.status = NotificationDeliveryAttemptStatus.SENT
        attempt.provider_reference = provider_reference
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["status", "provider_reference", "completed_at"])
        outbox.mark_processed()


@shared_task
def reap_stale_notifications():
    reap_stale(NotificationOutbox.objects.all())
