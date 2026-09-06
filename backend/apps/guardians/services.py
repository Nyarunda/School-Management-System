from django.core.exceptions import ValidationError

from apps.activity.services import record_activity
from apps.tenancy.services import require_same_tenant

from .models import Guardian, StudentGuardian


def link_guardian(*, student, guardian, relationship, actor=None, primary=False, emergency_contact=False):
    require_same_tenant(tenant=student.tenant, guardian=guardian)
    link = StudentGuardian.objects.create(
        tenant=student.tenant,
        student=student,
        guardian=guardian,
        relationship=relationship,
        is_primary=primary,
        is_emergency_contact=emergency_contact,
    )
    record_activity(
        tenant=student.tenant,
        actor=actor,
        action="guardian.added",
        resource_type="student",
        resource_id=str(student.id),
        metadata={"guardian_id": str(guardian.id)},
    )
    return link