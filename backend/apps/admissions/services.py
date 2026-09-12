from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.activity.services import record_activity
from apps.students.models import Student
from apps.tenancy.services import require_same_tenant

from .models import Application, ApplicationStatus


ALLOWED_TRANSITIONS = {
    ApplicationStatus.DRAFT: {ApplicationStatus.SUBMITTED},
    ApplicationStatus.SUBMITTED: {ApplicationStatus.UNDER_REVIEW},
    ApplicationStatus.UNDER_REVIEW: {ApplicationStatus.ACCEPTED, ApplicationStatus.REJECTED},
    ApplicationStatus.ACCEPTED: {ApplicationStatus.ENROLLED},
}


def transition_application(*, application, status, reviewer=None):
    if status not in ALLOWED_TRANSITIONS.get(application.status, set()):
        raise ValidationError(f"Cannot move application from {application.status} to {status}")
    application.status = status
    if reviewer is not None:
        application.reviewed_by = reviewer
        application.reviewed_at = timezone.now()
    application.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    record_activity(
        tenant=application.tenant,
        actor=reviewer,
        action=f"application.{status.lower()}",
        resource_type="application",
        resource_id=str(application.id),
    )
    return application


@transaction.atomic
def enroll_application(*, application, admission_number, campus=None, actor=None):
    if application.status != ApplicationStatus.ACCEPTED:
        raise ValidationError("Only accepted applications can be enrolled")
    if application.campus is not None:
        require_same_tenant(tenant=application.tenant, campus=application.campus)
    if campus is not None:
        require_same_tenant(tenant=application.tenant, campus=campus)
    student = Student.objects.create(
        tenant=application.tenant,
        admission_number=admission_number,
        first_name=application.first_name,
        last_name=application.last_name,
        date_of_birth=application.date_of_birth,
        campus=campus or application.campus,
    )
    application.status = ApplicationStatus.ENROLLED
    application.save(update_fields=["status"])
    record_activity(
        tenant=application.tenant,
        actor=actor,
        action="student.enrolled",
        resource_type="student",
        resource_id=str(student.id),
        metadata={"application_id": str(application.id)},
    )
    return student