import logging

from celery import shared_task
from django.utils import timezone

from apps.activity.durable_work import claim_due, reap_stale

from .gateways import resolve_gateway
from .models import NotificationDeliveryAttempt, NotificationDeliveryAttemptStatus, NotificationEvent, NotificationOutbox
from .services import expand_notification_event

logger = logging.getLogger(__name__)


@shared_task
def expand_pending_notification_events():
    """Second stage of the durable pipeline: turns each due NotificationEvent
    into zero or more NotificationOutbox rows via expand_notification_event.
    All rule/recipient/template-resolution risk lives here, never in the
    publish_notification_event call inside a business transaction.
    """
    for event in claim_due(NotificationEvent.objects.all(), limit=200):
        try:
            expand_notification_event(event=event)
        except Exception as error:
            event.mark_failed(error)
            continue
        event.mark_processed()


@shared_task
def reap_stale_notification_events():
    reap_stale(NotificationEvent.objects.all())


@shared_task
def dispatch_pending_notifications():
    """subject/body were already rendered at expansion time and stored in
    outbox.context -- dispatch never needs to re-resolve a NotificationRule/
    NotificationTemplate, so a template edited after a notification is
    queued never changes an already-queued message's content (the same
    snapshot-at-commit-time discipline used elsewhere in this codebase,
    e.g. Leave's LeaveRequestApproval).
    """
    for outbox in claim_due(NotificationOutbox.objects.all(), limit=200):
        attempt = NotificationDeliveryAttempt.objects.create(
            tenant=outbox.tenant, notification=outbox, attempt_number=outbox.attempts, provider=outbox.channel,
        )
        try:
            gateway = resolve_gateway(tenant=outbox.tenant, channel=outbox.channel)
            provider_reference = gateway.send(outbox=outbox)
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
