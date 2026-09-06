import uuid

from django.db import models

from apps.activity.durable_work import DurableWorkModel
from apps.tenancy.models import TenantOwnedModel


class NotificationChannel(models.TextChoices):
    SMS = "SMS", "SMS"
    EMAIL = "EMAIL", "Email"


class NotificationOutbox(TenantOwnedModel, DurableWorkModel):
    """A durable, Celery-consumed queue of outbound notifications. Enqueued
    from within the same transaction as the triggering business event (see
    services.enqueue_notification) so it can never diverge from the state
    that caused it. Sending itself is stubbed in this milestone -- see
    tasks.dispatch_pending_notifications.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    recipient = models.CharField(max_length=120)
    message_type = models.CharField(max_length=60)  # a template/category key, e.g. "invoice_issued"
    context = models.JSONField(default=dict)  # keep to template parameters only, not arbitrary payloads
    idempotency_key = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "idempotency_key"], name="unique_notification_idempotency_per_tenant"),
        ]
        indexes = [models.Index(fields=["tenant", "status", "available_at"])]
