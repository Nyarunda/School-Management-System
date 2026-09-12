"""The controlled list of business events this app can be asked to notify
on, plus the recipient resolvers a NotificationRule can route to.

Deliberately a Python constant, not a DB-backed model: mirrors this
codebase's existing precedent for Role.permissions (an unvalidated JSONField
checked ad hoc per service function, never a centrally-editable catalogue).
A tenant can configure *whether/how* to notify on an event via
NotificationRule, but never invent a new event code -- that requires a code
change (a new business-service call to publish_notification_event plus an
entry here), which is exactly what "don't let users type arbitrary event
names" means in practice.

Each entry is a real contract, not just a label: `recipient_refs` maps the
reference name a publisher must pass to the model class expand_pending_
notification_events re-resolves it with; `context_fields` are the caller-
supplied template variables the event guarantees; `recipient_types` are the
only recipient types a rule may target for this event. create_notification_
rule validates a new rule's recipient_type and template variables against
this contract at creation time -- an administrator can't wire a payment
template onto a leave-approval event and only discover the mismatch when it
actually fires.

Only 3 events are wired this milestone -- every other event named in the
original design brief is a documented future extension, added incrementally
alongside its own business-service integration.
"""
from django.db.models import Q

from apps.guardians.models import StudentGuardian
from apps.staff.models import Employee
from apps.students.models import Student

from .models import GuardianRecipientPolicy, NotificationChannel, NotificationRecipientType

EVENT_CATALOGUE = {
    "finance.payment.received": {
        "recipient_refs": {"student": Student},
        "context_fields": {"amount", "receipt_number"},
        "recipient_types": {NotificationRecipientType.GUARDIAN},
    },
    "leave.request.approved": {
        "recipient_refs": {"employee": Employee},
        "context_fields": {"start_date", "end_date"},
        "recipient_types": {NotificationRecipientType.EMPLOYEE},
    },
    "attendance.student.absent": {
        "recipient_refs": {"student": Student},
        "context_fields": {"session_date"},
        "recipient_types": {NotificationRecipientType.GUARDIAN},
    },
}

# Template variables a resolver itself supplies (from the resolved recipient,
# not the publisher's context) -- added to context_fields when validating a
# rule's template at creation time.
RESOLVER_CONTEXT_FIELDS = {
    NotificationRecipientType.GUARDIAN: {"guardian_name"},
    NotificationRecipientType.EMPLOYEE: {"employee_name"},
}


def allowed_context_fields(*, event_code, recipient_type):
    return EVENT_CATALOGUE[event_code]["context_fields"] | RESOLVER_CONTEXT_FIELDS.get(recipient_type, set())


def _resolve_guardian_recipients(*, tenant, channel, recipient_refs, policy):
    """`policy` (a GuardianRecipientPolicy value) is rule-level configuration,
    not a hardcoded default -- see NotificationRule.recipient_policy. IN_APP
    is never reached here: create_notification_rule rejects (GUARDIAN,
    IN_APP) outright, since guardians have no User account in this codebase.
    """
    student = recipient_refs["student"]
    guardian_filter = Q(is_primary=True)
    if policy == GuardianRecipientPolicy.PRIMARY_AND_EMERGENCY:
        guardian_filter |= Q(is_emergency_contact=True)
    links = StudentGuardian.objects.filter(tenant=tenant, student=student).filter(guardian_filter).select_related("guardian")
    results = []
    for link in links:
        guardian = link.guardian
        if channel == NotificationChannel.SMS:
            contact = guardian.phone_number
        elif channel == NotificationChannel.EMAIL:
            contact = guardian.email
        else:
            continue
        if not contact:
            continue
        results.append((contact, {"guardian_name": guardian.full_name}))
    return results


def _resolve_employee_recipients(*, tenant, channel, recipient_refs, policy):
    """IN_APP resolves via Employee.user_account -- if the employee has no
    linked account, the recipient list is simply empty (no error), matching
    Staff's existing treatment of an unlinked employee as a normal state.
    """
    employee: Employee = recipient_refs["employee"]
    if channel == NotificationChannel.SMS:
        contact = employee.phone_number
    elif channel == NotificationChannel.EMAIL:
        contact = employee.email
    elif channel == NotificationChannel.IN_APP:
        contact = employee.user_account_id
    else:
        contact = None
    if not contact:
        return []
    extra_context = {"employee_name": employee.full_name}
    if channel == NotificationChannel.IN_APP:
        return [(employee.user_account_id, extra_context)]
    return [(contact, extra_context)]


RECIPIENT_RESOLVERS = {
    NotificationRecipientType.GUARDIAN: _resolve_guardian_recipients,
    NotificationRecipientType.EMPLOYEE: _resolve_employee_recipients,
}
