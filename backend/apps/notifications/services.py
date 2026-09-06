from .models import NotificationOutbox


def enqueue_notification(*, tenant, channel, recipient, message_type, idempotency_key, context=None):
    """Call from within the same transaction as the triggering business
    event, so the outbox row can never diverge from the state that caused
    it. idempotency_key is caller-supplied (e.g. f"invoice-issued:{invoice.id}:{guardian.id}")
    so a retried outer operation can't double-enqueue the same notification
    -- get_or_create is safe here since every caller must supply a real
    key (unlike log_mpesa_callback's blank-key case in apps.finance).
    No caller is wired up in this slice; see the 5D plan's Non-goals.
    """
    outbox, _ = NotificationOutbox.objects.get_or_create(
        tenant=tenant, idempotency_key=idempotency_key,
        defaults={"channel": channel, "recipient": recipient, "message_type": message_type, "context": context or {}},
    )
    return outbox
