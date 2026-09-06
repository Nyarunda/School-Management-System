from django.core.exceptions import ValidationError
from django.db import IntegrityError

from .models import NotificationOutbox


def enqueue_notification(*, tenant, channel, recipient, message_type, idempotency_key, context=None):
    """Call from within the same transaction as the triggering business
    event, so the outbox row can never diverge from the state that caused
    it. idempotency_key is caller-supplied (e.g. f"invoice-issued:{invoice.id}:{guardian.id}")
    so a retried outer operation can't double-enqueue the same notification.
    Mirrors _matching_incoming_replay's shape in apps/finance/services.py:
    a reused key with the *same* payload replays the existing row; a reused
    key with a *different* payload is a real bug (e.g. a caller changed the
    recipient without changing the key) and must not be silently swallowed
    by get_or_create. No caller is wired up in this slice; see the 5D plan's
    Non-goals.
    """
    context = context or {}
    existing = NotificationOutbox.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
    if existing is not None:
        return _matching_notification_replay(
            existing=existing, channel=channel, recipient=recipient, message_type=message_type, context=context,
        )
    try:
        return NotificationOutbox.objects.create(
            tenant=tenant, channel=channel, recipient=recipient, message_type=message_type,
            idempotency_key=idempotency_key, context=context,
        )
    except IntegrityError:
        replay = NotificationOutbox.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
        if replay is not None:
            return _matching_notification_replay(
                existing=replay, channel=channel, recipient=recipient, message_type=message_type, context=context,
            )
        raise


def _matching_notification_replay(*, existing, channel, recipient, message_type, context):
    if (existing.channel, existing.recipient, existing.message_type, existing.context) != (channel, recipient, message_type, context):
        raise ValidationError("Idempotency key already used with different notification details")
    return existing
