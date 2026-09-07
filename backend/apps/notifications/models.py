import uuid

from django.db import models

from apps.activity.durable_work import DurableWorkModel
from apps.finance.fields import EncryptedCharField
from apps.tenancy.models import TenantOwnedModel, User


class NotificationChannel(models.TextChoices):
    SMS = "SMS", "SMS"
    EMAIL = "EMAIL", "Email"
    IN_APP = "IN_APP", "In-app"


class CommunicationSetup(TenantOwnedModel):
    """One per tenant. Absence of a row (unconfigured tenant) is treated as
    notifications_enabled=True with no quiet hours -- see publish_notification_event.
    """

    notifications_enabled = models.BooleanField(default=True)
    default_country_code = models.CharField(max_length=5, blank=True)
    quiet_hours_enabled = models.BooleanField(default=False)
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="unique_communication_setup_per_tenant")]


class CommunicationChannel(TenantOwnedModel):
    """Per-tenant on/off switch for a channel. Off by default -- a tenant
    must explicitly opt in before publish_notification_event enqueues
    anything on that channel, even if a NotificationRule exists for it.
    """

    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "channel"], name="unique_channel_per_tenant")]


class NotificationProviderConfig(TenantOwnedModel):
    """One active provider per (tenant, channel) this milestone. Credential
    shape is generic (api key/secret) -- sufficient for the stub gateway and
    a first real provider; a provider needing a different credential shape
    is a future extension, mirroring how TenantMpesaConfiguration's fields
    are specific to that one gateway.
    """

    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    provider = models.CharField(max_length=40)
    sender_id = models.CharField(max_length=40, blank=True)
    encrypted_api_key = EncryptedCharField(max_length=1024, blank=True)
    encrypted_api_secret = EncryptedCharField(max_length=1024, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "channel"], name="unique_provider_per_channel_per_tenant"),
        ]


class NotificationTemplate(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=120)
    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_template_code_per_tenant")]


class NotificationRecipientType(models.TextChoices):
    GUARDIAN = "GUARDIAN", "Guardian"
    EMPLOYEE = "EMPLOYEE", "Employee"


class NotificationRule(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_code = models.CharField(max_length=80)
    recipient_type = models.CharField(max_length=20, choices=NotificationRecipientType.choices)
    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    template = models.ForeignKey(NotificationTemplate, on_delete=models.PROTECT, related_name="rules")
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "event_code", "recipient_type", "channel"],
                name="unique_rule_per_event_recipient_channel",
            ),
        ]


class NotificationOutbox(TenantOwnedModel, DurableWorkModel):
    """A durable, Celery-consumed queue of outbound notifications. Enqueued
    from within the same transaction as the triggering business event (see
    services.enqueue_notification/publish_notification_event) so it can
    never diverge from the state that caused it. For IN_APP rows, `recipient`
    holds str(user.id) rather than a phone/email -- InAppGateway resolves it
    back to a User when the row is claimed.
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


class NotificationDeliveryAttemptStatus(models.TextChoices):
    PROCESSING = "PROCESSING", "Processing"
    SENT = "SENT", "Sent"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Failed"


class NotificationDeliveryAttempt(TenantOwnedModel):
    """One row per dispatch attempt against a NotificationOutbox row -- a
    per-attempt audit trail distinct from the outbox's own mutable
    attempts/last_error summary. DELIVERED is modeled but unreachable this
    milestone: no provider delivery-confirmation webhook exists yet
    (mirrors M-Pesa's own callback as a separate, later concern).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(NotificationOutbox, on_delete=models.CASCADE, related_name="delivery_attempts")
    attempt_number = models.PositiveSmallIntegerField()
    provider = models.CharField(max_length=40)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=NotificationDeliveryAttemptStatus.choices, default=NotificationDeliveryAttemptStatus.PROCESSING,
    )
    provider_reference = models.CharField(max_length=120, blank=True)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.TextField(blank=True)


class UserNotification(TenantOwnedModel):
    """The IN_APP channel's delivery target -- created by InAppGateway when
    an IN_APP NotificationOutbox row is claimed, not written directly by
    business domains.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=150)
    message = models.TextField()
    resource_type = models.CharField(max_length=80, blank=True)
    resource_id = models.CharField(max_length=80, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "user", "read_at", "-created_at"])]
