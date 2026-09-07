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

Only 3 events are wired this milestone (see the Leave/Milestone-18 plan's
Non-goals) -- every other event named in the original design brief is a
documented future extension, added incrementally alongside its own
business-service integration.
"""
from django.db.models import Q

from apps.guardians.models import StudentGuardian
from apps.staff.models import Employee

from .models import NotificationChannel, NotificationRecipientType

EVENT_CATALOGUE = {
    "finance.payment.received": {"recipient_refs": {"student"}},
    "leave.request.approved": {"recipient_refs": {"employee"}},
    "attendance.student.absent": {"recipient_refs": {"student"}},
}


def _resolve_guardian_recipients(*, tenant, channel, recipient_refs):
    """Only guardians flagged is_primary or is_emergency_contact for the
    student -- a deliberate default (not every linked guardian) to avoid
    over-notifying a household. Not a per-tenant setting this milestone.
    IN_APP is never reached here: create_notification_rule rejects
    (GUARDIAN, IN_APP) outright, since guardians have no User account in
    this codebase.
    """
    student = recipient_refs["student"]
    links = (
        StudentGuardian.objects.filter(tenant=tenant, student=student)
        .filter(Q(is_primary=True) | Q(is_emergency_contact=True))
        .select_related("guardian")
    )
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


def _resolve_employee_recipients(*, tenant, channel, recipient_refs):
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
        contact = str(employee.user_account_id) if employee.user_account_id else ""
    else:
        contact = ""
    if not contact:
        return []
    return [(contact, {"employee_name": employee.full_name})]


RECIPIENT_RESOLVERS = {
    NotificationRecipientType.GUARDIAN: _resolve_guardian_recipients,
    NotificationRecipientType.EMPLOYEE: _resolve_employee_recipients,
}
