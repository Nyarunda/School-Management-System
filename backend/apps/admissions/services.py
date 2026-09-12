from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academics.services import enroll_student
from apps.activity.services import record_activity
from apps.documents.services import delete_document, upload_document
from apps.students.models import Student
from apps.tenancy.services import require_permission, require_same_tenant

from .models import Application, ApplicationDocument, ApplicationStatus


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
def enroll_application(*, user, tenant, application, admission_number, academic_year, class_group, term=None, campus=None, actor=None):
    """The single authoritative Application -> Student -> academic
    placement orchestration -- closes the RC Area 3 critical journey gap
    where an "ENROLLED" application previously produced a Student with no
    class/section/academic-year placement. Reuses
    apps.academics.services.enroll_student's own validation/IntegrityError
    handling for the placement half rather than duplicating it, so the
    acting user needs both "admissions.enroll" and
    "academics.students.enroll" -- a role-configuration detail, not a new
    permission-chaining mechanism.
    """
    membership = require_permission(user=user, tenant=tenant, permission="admissions.enroll")
    require_same_tenant(tenant=tenant, application=application)

    locked_application = Application.objects.select_for_update().get(tenant=tenant, pk=application.pk)
    if locked_application.status != ApplicationStatus.ACCEPTED:
        raise ValidationError("Only accepted applications can be enrolled")

    require_same_tenant(tenant=tenant, academic_year=academic_year, class_group=class_group)
    if term is not None:
        require_same_tenant(tenant=tenant, term=term)
    if campus is not None:
        require_same_tenant(tenant=tenant, campus=campus)
    if locked_application.campus is not None:
        require_same_tenant(tenant=tenant, campus=locked_application.campus)

    resolved_campus = campus or locked_application.campus or class_group.campus
    if resolved_campus.id != class_group.campus_id:
        raise ValidationError("Class must belong to the selected campus")

    if membership.campus_id is not None:
        campuses_in_play = {
            c.id for c in (locked_application.campus, campus, class_group.campus) if c is not None
        }
        if campuses_in_play - {membership.campus_id}:
            raise ValidationError("User is not authorized for this campus")

    student = Student.objects.create(
        tenant=tenant,
        admission_number=admission_number,
        first_name=locked_application.first_name,
        last_name=locked_application.last_name,
        date_of_birth=locked_application.date_of_birth,
        campus=resolved_campus,
    )

    enrollment = enroll_student(
        user=user, tenant=tenant, student=student, academic_year=academic_year,
        academic_level=class_group.academic_level, class_group=class_group,
        campus=resolved_campus, term=term,
    )

    locked_application.status = ApplicationStatus.ENROLLED
    locked_application.save(update_fields=["status"])
    record_activity(
        tenant=tenant,
        actor=actor or user,
        action="application.enrolled",
        resource_type="application",
        resource_id=str(locked_application.id),
        metadata={"student_id": str(student.id), "enrollment_id": str(enrollment.id)},
    )
    return student, enrollment


def add_application_document(*, user, tenant, application, document_type, file_obj, original_filename, content_type, actor=None):
    """Service-layer only -- application review/document management has no
    API layer of its own (only enroll_application is exposed, via
    apps.admissions.api).
    """
    require_permission(user=user, tenant=tenant, permission="admissions.document.manage")
    require_same_tenant(tenant=tenant, application=application)

    stored = upload_document(
        tenant=tenant, uploaded_by=actor or user, file_obj=file_obj,
        original_filename=original_filename, content_type=content_type,
    )
    document = ApplicationDocument.objects.create(
        tenant=tenant, application=application, document_type=document_type, document=stored,
    )
    record_activity(
        tenant=tenant, actor=actor or user, action="application.document_added",
        resource_type="application", resource_id=str(application.id),
        metadata={"document_id": str(document.id)},
    )
    return document


def delete_application_document(*, user, tenant, application_document, actor=None):
    require_permission(user=user, tenant=tenant, permission="admissions.document.manage")
    require_same_tenant(tenant=tenant, application=application_document.application)

    if application_document.document is not None:
        delete_document(document=application_document.document)
    application_id = application_document.application_id
    document_id = application_document.id
    application_document.delete()
    record_activity(
        tenant=tenant, actor=actor or user, action="application.document_deleted",
        resource_type="application", resource_id=str(application_id),
        metadata={"document_id": str(document_id)},
    )