import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.template import Context, Template, TemplateSyntaxError
from django.utils import timezone

from apps.activity.durable_work import DurableWorkStatus
from apps.tenancy.services import require_permission, require_same_tenant

from .catalogue import EVENT_CATALOGUE, RECIPIENT_RESOLVERS
from .models import (
    CommunicationChannel,
    CommunicationSetup,
    NotificationChannel,
    NotificationOutbox,
    NotificationProviderConfig,
    NotificationRecipientType,
    NotificationRule,
    NotificationTemplate,
)

_VALID_RECIPIENT_TYPES = {choice for choice, _ in NotificationRecipientType.choices}


def enqueue_notification(*, tenant, channel, recipient, message_type, idempotency_key, context=None):
    """Call from within the same transaction as the triggering business
    event, so the outbox row can never diverge from the state that caused
    it. idempotency_key is caller-supplied (e.g. f"invoice-issued:{invoice.id}:{guardian.id}")
    so a retried outer operation can't double-enqueue the same notification.
    Mirrors _matching_incoming_replay's shape in apps/finance/services.py:
    a reused key with the *same* payload replays the existing row; a reused
    key with a *different* payload is a real bug (e.g. a caller changed the
    recipient without changing the key) and must not be silently swallowed
    by get_or_create. The primary caller is publish_notification_event
    below; enqueue_notification remains directly callable for anything
    that wants to bypass rule/template resolution entirely.
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


# --- Setup -------------------------------------------------------------

SETUP_UPDATABLE_FIELDS = ["notifications_enabled", "default_country_code", "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end"]


def configure_communication_setup(*, user, tenant, **fields):
    require_permission(user=user, tenant=tenant, permission="notifications.setup.manage")
    unknown = set(fields) - set(SETUP_UPDATABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Unknown setup field(s): {', '.join(sorted(unknown))}")
    setup, _ = CommunicationSetup.objects.get_or_create(tenant=tenant)
    for field, value in fields.items():
        setattr(setup, field, value)
    setup.save()
    return setup


def set_channel_enabled(*, user, tenant, channel, enabled):
    require_permission(user=user, tenant=tenant, permission="notifications.setup.manage")
    channel_row, _ = CommunicationChannel.objects.get_or_create(tenant=tenant, channel=channel)
    channel_row.enabled = enabled
    channel_row.save(update_fields=["enabled"])
    return channel_row


def configure_provider(*, user, tenant, channel, provider, sender_id="", api_key=None, api_secret=None, is_active=True):
    """Idempotent: safe to call again to rotate credentials, mirroring
    configure_mpesa_gateway. A blank api_key/api_secret leaves the existing
    encrypted value untouched, so a caller can update sender_id alone
    without re-supplying secrets already on file.
    """
    require_permission(user=user, tenant=tenant, permission="notifications.setup.manage")
    config = NotificationProviderConfig.objects.filter(tenant=tenant, channel=channel).first()
    if config is None:
        config = NotificationProviderConfig(tenant=tenant, channel=channel)
    config.provider = provider
    config.sender_id = sender_id
    config.is_active = is_active
    if api_key is not None:
        config.encrypted_api_key = api_key
    if api_secret is not None:
        config.encrypted_api_secret = api_secret
    config.save()
    return config


# --- Templates -----------------------------------------------------------

TEMPLATE_UPDATABLE_FIELDS = ["name", "subject", "body", "is_active"]


def create_notification_template(*, user, tenant, code, name, channel, body, subject="", is_active=True):
    require_permission(user=user, tenant=tenant, permission="notifications.templates.manage")
    code = " ".join(code.strip().upper().split())
    if not code:
        raise ValidationError("Template code cannot be blank")
    try:
        return NotificationTemplate.objects.create(
            tenant=tenant, code=code, name=name, channel=channel, subject=subject, body=body, is_active=is_active,
        )
    except IntegrityError as error:
        raise ValidationError("A template with this code already exists for this tenant") from error


def update_notification_template(*, user, tenant, template, **fields):
    require_permission(user=user, tenant=tenant, permission="notifications.templates.manage")
    require_same_tenant(tenant=tenant, template=template)
    unknown = set(fields) - set(TEMPLATE_UPDATABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Cannot update field(s): {', '.join(sorted(unknown))}")
    for field, value in fields.items():
        setattr(template, field, value)
    template.save(update_fields=list(fields))
    return template


# --- Rules -----------------------------------------------------------------

RULE_UPDATABLE_FIELDS = ["template", "enabled"]


def create_notification_rule(*, user, tenant, event_code, recipient_type, channel, template, enabled=True):
    require_permission(user=user, tenant=tenant, permission="notifications.rules.manage")
    require_same_tenant(tenant=tenant, template=template)
    if event_code not in EVENT_CATALOGUE:
        raise ValidationError(f"Unknown event code: {event_code}")
    if recipient_type not in _VALID_RECIPIENT_TYPES:
        raise ValidationError(f"Unknown recipient type: {recipient_type}")
    if template.channel != channel:
        raise ValidationError("Template channel must match the rule's channel")
    if recipient_type == NotificationRecipientType.GUARDIAN and channel == NotificationChannel.IN_APP:
        raise ValidationError("Guardians have no in-app account to notify")
    try:
        return NotificationRule.objects.create(
            tenant=tenant, event_code=event_code, recipient_type=recipient_type, channel=channel,
            template=template, enabled=enabled,
        )
    except IntegrityError as error:
        raise ValidationError("A rule for this event, recipient type, and channel already exists") from error


def update_notification_rule(*, user, tenant, rule, **fields):
    require_permission(user=user, tenant=tenant, permission="notifications.rules.manage")
    require_same_tenant(tenant=tenant, rule=rule)
    unknown = set(fields) - set(RULE_UPDATABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Cannot update field(s): {', '.join(sorted(unknown))}")
    if "template" in fields:
        require_same_tenant(tenant=tenant, template=fields["template"])
        if fields["template"].channel != rule.channel:
            raise ValidationError("Template channel must match the rule's channel")
    for field, value in fields.items():
        setattr(rule, field, value)
    rule.save(update_fields=list(fields))
    return rule


# --- Publishing --------------------------------------------------------

_TEMPLATE_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _render_template(text, context):
    """Renders {{ variable }} placeholders via Django's template engine, but
    fails clearly (ValidationError) if a placeholder has no matching context
    key, rather than Django's own default of silently rendering it blank.
    """
    if not text:
        return ""
    referenced = set(_TEMPLATE_VAR_PATTERN.findall(text))
    missing = referenced - set(context)
    if missing:
        raise ValidationError(f"Template references undefined variable(s): {', '.join(sorted(missing))}")
    try:
        return Template(text).render(Context(context))
    except TemplateSyntaxError as error:
        raise ValidationError(f"Template syntax error: {error}") from error


def publish_notification_event(*, tenant, event_code, dedupe_key, context, recipient_refs, actor=None):
    """The cross-domain integration point -- call from within the same
    @transaction.atomic block as the triggering business write, right after
    its record_activity() call, so the notification intent commits
    atomically with the business event (never an HTTP call inside the
    transaction -- that happens later, in tasks.dispatch_pending_notifications).

    event_code must be a key in catalogue.EVENT_CATALOGUE -- an unknown code
    is a programmer error (a business service calling this with a typo or an
    event nothing has been wired for), not a user-facing validation failure.
    An absent CommunicationSetup row is treated as notifications_enabled=True
    (an unconfigured tenant still gets notifications, matching this
    project's "unconfigured tenant behaves like the sane default" convention
    from Leave's resolve_leave_year).
    """
    if event_code not in EVENT_CATALOGUE:
        raise ValueError(f"Unknown event code: {event_code}")

    setup = CommunicationSetup.objects.filter(tenant=tenant).first()
    if setup is not None and not setup.notifications_enabled:
        return []

    enabled_channels = set(CommunicationChannel.objects.filter(tenant=tenant, enabled=True).values_list("channel", flat=True))
    if not enabled_channels:
        return []

    rules = NotificationRule.objects.filter(tenant=tenant, event_code=event_code, enabled=True).select_related("template")

    created = []
    for rule in rules:
        if rule.channel not in enabled_channels:
            continue
        resolver = RECIPIENT_RESOLVERS.get(rule.recipient_type)
        if resolver is None:
            continue
        for contact, extra_context in resolver(tenant=tenant, channel=rule.channel, recipient_refs=recipient_refs):
            merged_context = {**context, **extra_context}
            subject = _render_template(rule.template.subject, merged_context)
            body = _render_template(rule.template.body, merged_context)
            idempotency_key = f"{dedupe_key}:{rule.recipient_type}:{rule.channel}:{contact}"
            outbox = enqueue_notification(
                tenant=tenant, channel=rule.channel, recipient=contact, message_type=rule.template.code,
                idempotency_key=idempotency_key, context={**merged_context, "subject": subject, "body": body},
            )
            created.append(outbox)
    return created


# --- Outbox management -----------------------------------------------------

def retry_notification(*, user, tenant, outbox):
    require_permission(user=user, tenant=tenant, permission="notifications.retry")
    require_same_tenant(tenant=tenant, outbox=outbox)
    if outbox.status != DurableWorkStatus.FAILED:
        raise ValidationError("Only failed notifications can be retried")
    outbox.status = DurableWorkStatus.PENDING
    outbox.available_at = timezone.now()
    outbox.last_error = ""
    outbox.save(update_fields=["status", "available_at", "last_error"])
    return outbox


# --- In-app inbox ------------------------------------------------------

def mark_user_notification_read(*, user, notification):
    if notification.user_id != user.id:
        raise ValidationError("You cannot mark another user's notification as read")
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return notification
