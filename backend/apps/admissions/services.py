from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

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


def add_application_document(*, user, tenant, application, document_type, file_obj, original_filename, content_type, actor=None):
    """Service-layer only -- Admissions has no API layer, consistent with
    the rest of this app.
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