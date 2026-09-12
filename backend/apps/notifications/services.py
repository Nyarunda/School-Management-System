import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.template import Context, Template, TemplateSyntaxError
from django.utils import timezone

from apps.activity.durable_work import DurableWorkStatus
from apps.activity.services import record_activity
from apps.tenancy.services import require_permission, require_same_tenant

from .catalogue import EVENT_CATALOGUE, RECIPIENT_RESOLVERS, allowed_context_fields
from .models import (
    CommunicationChannel,
    CommunicationSetup,
    GuardianRecipientPolicy,
    NotificationChannel,
    NotificationEvent,
    NotificationOutbox,
    NotificationProviderConfig,
    NotificationRecipientType,
    NotificationRule,
    NotificationTemplate,
)

_VALID_RECIPIENT_TYPES = {choice for choice, _ in NotificationRecipientType.choices}
_VALID_GUARDIAN_POLICIES = {choice for choice, _ in GuardianRecipientPolicy.choices}


def enqueue_notification(*, tenant, channel, message_type, idempotency_key, context=None, recipient="", recipient_user_id=None):
    """Low-level primitive: creates (or idempotently replays) one
    NotificationOutbox row. Called from expand_notification_event below --
    business domains should call publish_notification_event instead, never
    this directly, so rule/template/recipient resolution always happens
    through the durable NotificationEvent boundary.
    """
    context = context or {}
    existing = NotificationOutbox.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
    if existing is not None:
        return _matching_notification_replay(
            existing=existing, channel=channel, recipient=recipient, recipient_user_id=recipient_user_id,
            message_type=message_type, context=context,
        )
    try:
        return NotificationOutbox.objects.create(
            tenant=tenant, channel=channel, recipient=recipient, recipient_user_id=recipient_user_id,
            message_type=message_type, idempotency_key=idempotency_key, context=context,
        )
    except IntegrityError:
        replay = NotificationOutbox.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
        if replay is not None:
            return _matching_notification_replay(
                existing=replay, channel=channel, recipient=recipient, recipient_user_id=recipient_user_id,
                message_type=message_type, context=context,
            )
        raise


def _matching_notification_replay(*, existing, channel, recipient, recipient_user_id, message_type, context):
    fingerprint = (existing.channel, existing.recipient, existing.recipient_user_id, existing.message_type, existing.context)
    if fingerprint != (channel, recipient, recipient_user_id, message_type, context):
        raise ValidationError("Idempotency key already used with different notification details")
    return existing


# --- Setup -------------------------------------------------------------

SETUP_UPDATABLE_FIELDS = ["notifications_enabled", "default_country_code"]


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
    without re-supplying secrets already on file. IN_APP is rejected -- it
    has no external provider, only the internal UserNotification write.
    """
    require_permission(user=user, tenant=tenant, permission="notifications.setup.manage")
    if channel == NotificationChannel.IN_APP:
        raise ValidationError("IN_APP has no external provider to configure")
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

RULE_UPDATABLE_FIELDS = ["template", "enabled", "recipient_policy"]

_TEMPLATE_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _referenced_template_variables(template):
    return set(_TEMPLATE_VAR_PATTERN.findall(template.subject)) | set(_TEMPLATE_VAR_PATTERN.findall(template.body))


def _validate_rule_template_contract(*, event_code, recipient_type, template):
    allowed = allowed_context_fields(event_code=event_code, recipient_type=recipient_type)
    unknown = _referenced_template_variables(template) - allowed
    if unknown:
        raise ValidationError(f"Template references variable(s) not provided by this event: {', '.join(sorted(unknown))}")


def create_notification_rule(
    *, user, tenant, event_code, recipient_type, channel, template, enabled=True, recipient_policy=None,
):
    require_permission(user=user, tenant=tenant, permission="notifications.rules.manage")
    require_same_tenant(tenant=tenant, template=template)
    if event_code not in EVENT_CATALOGUE:
        raise ValidationError(f"Unknown event code: {event_code}")
    if recipient_type not in _VALID_RECIPIENT_TYPES:
        raise ValidationError(f"Unknown recipient type: {recipient_type}")
    if recipient_type not in EVENT_CATALOGUE[event_code]["recipient_types"]:
        raise ValidationError(f"{recipient_type} is not a valid recipient type for {event_code}")
    if template.channel != channel:
        raise ValidationError("Template channel must match the rule's channel")
    if recipient_type == NotificationRecipientType.GUARDIAN and channel == NotificationChannel.IN_APP:
        raise ValidationError("Guardians have no in-app account to notify")

    if recipient_type == NotificationRecipientType.GUARDIAN:
        recipient_policy = recipient_policy or GuardianRecipientPolicy.PRIMARY_AND_EMERGENCY
        if recipient_policy not in _VALID_GUARDIAN_POLICIES:
            raise ValidationError(f"Unknown recipient policy: {recipient_policy}")
    elif recipient_policy:
        raise ValidationError("recipient_policy only applies to GUARDIAN rules")
    else:
        recipient_policy = ""

    _validate_rule_template_contract(event_code=event_code, recipient_type=recipient_type, template=template)
    try:
        return NotificationRule.objects.create(
            tenant=tenant, event_code=event_code, recipient_type=recipient_type, recipient_policy=recipient_policy,
            channel=channel, template=template, enabled=enabled,
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
        _validate_rule_template_contract(event_code=rule.event_code, recipient_type=rule.recipient_type, template=fields["template"])
    if "recipient_policy" in fields:
        if rule.recipient_type != NotificationRecipientType.GUARDIAN:
            raise ValidationError("recipient_policy only applies to GUARDIAN rules")
        if fields["recipient_policy"] not in _VALID_GUARDIAN_POLICIES:
            raise ValidationError(f"Unknown recipient policy: {fields['recipient_policy']}")
    for field, value in fields.items():
        setattr(rule, field, value)
    rule.save(update_fields=list(fields))
    return rule


# --- Publishing (durable event boundary) --------------------------------

def publish_notification_event(*, tenant, event_code, dedupe_key, context, recipient_refs, actor=None):
    """The cross-domain integration point -- call from within the same
    @transaction.atomic block as the triggering business write, right after
    its record_activity() call. This only writes a durable NotificationEvent
    fact; no rule lookup, recipient resolution, or template rendering
    happens here, so a tenant's broken notification configuration can never
    roll back the business transaction that just committed. Expansion into
    actual NotificationOutbox rows happens later, asynchronously, in
    expand_notification_event (see tasks.expand_pending_notification_events).

    event_code must be a key in catalogue.EVENT_CATALOGUE, and recipient_refs
    keys must match that event's declared ref names -- both are programmer
    errors (ValueError), not user-facing validation failures.
    """
    if event_code not in EVENT_CATALOGUE:
        raise ValueError(f"Unknown event code: {event_code}")
    declared_refs = EVENT_CATALOGUE[event_code]["recipient_refs"]
    unexpected = set(recipient_refs) - set(declared_refs)
    if unexpected:
        raise ValueError(f"Unexpected recipient_refs for {event_code}: {', '.join(sorted(unexpected))}")

    serialized_refs = {name: str(obj.pk) for name, obj in recipient_refs.items()}
    existing = NotificationEvent.objects.filter(tenant=tenant, dedupe_key=dedupe_key).first()
    if existing is not None:
        return _matching_event_replay(existing=existing, event_code=event_code, context=context, recipient_refs=serialized_refs)
    try:
        return NotificationEvent.objects.create(
            tenant=tenant, event_code=event_code, dedupe_key=dedupe_key, context=context,
            recipient_refs=serialized_refs, actor=actor,
        )
    except IntegrityError:
        replay = NotificationEvent.objects.filter(tenant=tenant, dedupe_key=dedupe_key).first()
        if replay is not None:
            return _matching_event_replay(existing=replay, event_code=event_code, context=context, recipient_refs=serialized_refs)
        raise


def _matching_event_replay(*, existing, event_code, context, recipient_refs):
    if (existing.event_code, existing.context, existing.recipient_refs) != (event_code, context, recipient_refs):
        raise ValidationError("Idempotency key already used with different event details")
    return existing


_TEMPLATE_RENDER_VAR_PATTERN = _TEMPLATE_VAR_PATTERN


def _render_template(text, context):
    """Renders {{ variable }} placeholders via Django's template engine, but
    fails clearly (ValidationError) if a placeholder has no matching context
    key, rather than Django's own default of silently rendering it blank.
    Runtime defense-in-depth alongside _validate_rule_template_contract's
    creation-time check -- the two should never disagree given the same
    catalogue, but this is what actually protects rendering either way.
    """
    if not text:
        return ""
    referenced = set(_TEMPLATE_RENDER_VAR_PATTERN.findall(text))
    missing = referenced - set(context)
    if missing:
        raise ValidationError(f"Template references undefined variable(s): {', '.join(sorted(missing))}")
    try:
        return Template(text).render(Context(context))
    except TemplateSyntaxError as error:
        raise ValidationError(f"Template syntax error: {error}") from error


def expand_notification_event(*, event):
    """Called once per claimed NotificationEvent by tasks.expand_pending_
    notification_events. Re-resolves recipient_refs into live model
    instances, then applies the same rule/channel/template/recipient-policy
    resolution publish_notification_event used to run inline -- now
    isolated per rule: a single rule's bad template or resolver error is
    caught and logged via record_activity, never aborting a sibling rule's
    expansion or the event itself. Returns the list of NotificationOutbox
    rows created (possibly empty, which is a normal outcome -- e.g. no
    channel enabled, or every matching rule was itself disabled/inactive).
    """
    tenant = event.tenant
    catalogue_entry = EVENT_CATALOGUE[event.event_code]

    recipient_refs = {}
    for name, model in catalogue_entry["recipient_refs"].items():
        stored_id = event.recipient_refs.get(name)
        if stored_id is None:
            continue
        try:
            recipient_refs[name] = model.objects.get(tenant=tenant, pk=stored_id)
        except model.DoesNotExist:
            record_activity(
                tenant=tenant, actor=event.actor, action="notification.event_expansion_failed",
                resource_type="notification_event", resource_id=str(event.id),
                metadata={"reason": f"{name} {stored_id} no longer exists"},
            )
            return []

    setup = CommunicationSetup.objects.filter(tenant=tenant).first()
    if setup is not None and not setup.notifications_enabled:
        return []
    enabled_channels = set(CommunicationChannel.objects.filter(tenant=tenant, enabled=True).values_list("channel", flat=True))
    if not enabled_channels:
        return []

    rules = NotificationRule.objects.filter(tenant=tenant, event_code=event.event_code, enabled=True).select_related("template")
    created = []
    for rule in rules:
        if rule.channel not in enabled_channels or not rule.template.is_active:
            continue
        resolver = RECIPIENT_RESOLVERS.get(rule.recipient_type)
        if resolver is None:
            continue
        try:
            recipients = resolver(
                tenant=tenant, channel=rule.channel, recipient_refs=recipient_refs, policy=rule.recipient_policy,
            )
            for contact, extra_context in recipients:
                merged_context = {**event.context, **extra_context}
                subject = _render_template(rule.template.subject, merged_context)
                body = _render_template(rule.template.body, merged_context)
                idempotency_key = f"{event.dedupe_key}:{rule.recipient_type}:{rule.channel}:{contact}"
                rendered_context = {**merged_context, "subject": subject, "body": body}
                if rule.channel == NotificationChannel.IN_APP:
                    outbox = enqueue_notification(
                        tenant=tenant, channel=rule.channel, recipient_user_id=contact, message_type=rule.template.code,
                        idempotency_key=idempotency_key, context=rendered_context,
                    )
                else:
                    outbox = enqueue_notification(
                        tenant=tenant, channel=rule.channel, recipient=contact, message_type=rule.template.code,
                        idempotency_key=idempotency_key, context=rendered_context,
                    )
                created.append(outbox)
        except ValidationError as error:
            messages = error.messages if hasattr(error, "messages") else [str(error)]
            record_activity(
                tenant=tenant, actor=event.actor, action="notification.rule_expansion_failed",
                resource_type="notification_rule", resource_id=str(rule.id),
                metadata={"event_id": str(event.id), "error": " ".join(messages)},
            )
            continue
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
