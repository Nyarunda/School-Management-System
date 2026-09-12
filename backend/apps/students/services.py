from django.core.exceptions import ValidationError

from apps.activity.services import record_activity
from apps.documents.services import delete_document, upload_document
from apps.tenancy.services import require_permission, require_same_tenant

from .models import Student, StudentDocument, StudentStatus


def _require_campus_scope(*, membership, campus_id):
    """RC Area 3: apps.students had no campus scoping anywhere, unlike
    apps.staff's identical helper (which this mirrors) or Attendance/
    Assessments/Leave/Timetable's own copies of the same pattern.
    """
    if membership.campus_id is not None and campus_id != membership.campus_id:
        raise ValidationError("User is not authorized for this campus")


STUDENT_TRANSITIONS = {
    StudentStatus.ACTIVE: {StudentStatus.SUSPENDED, StudentStatus.TRANSFERRED, StudentStatus.WITHDRAWN, StudentStatus.GRADUATED},
    StudentStatus.SUSPENDED: {StudentStatus.ACTIVE, StudentStatus.WITHDRAWN},
    StudentStatus.TRANSFERRED: set(),
    StudentStatus.WITHDRAWN: set(),
    StudentStatus.GRADUATED: set(),
}


def change_student_status(*, student, status, actor=None):
    if status not in STUDENT_TRANSITIONS.get(student.status, set()):
        raise ValidationError(f"Cannot move student from {student.status} to {status}")
    student.status = status
    student.save(update_fields=["status"])
    record_activity(
        tenant=student.tenant,
        actor=actor,
        action=f"student.{status.lower()}",
        resource_type="student",
        resource_id=str(student.id),
    )
    return student


def place_student(*, student, campus, actor=None):
    require_same_tenant(tenant=student.tenant, campus=campus)
    student.campus = campus
    student.save(update_fields=["campus"])
    record_activity(
        tenant=student.tenant,
        actor=actor,
        action="student.placed",
        resource_type="student",
        resource_id=str(student.id),
        metadata={"campus_id": str(campus.id)},
    )
    return student


def add_student_document(*, user, tenant, student, document_type, file_obj, original_filename, content_type, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="students.document.manage")
    require_same_tenant(tenant=tenant, student=student)
    _require_campus_scope(membership=membership, campus_id=student.campus_id)

    stored = upload_document(
        tenant=tenant, uploaded_by=actor or user, file_obj=file_obj,
        original_filename=original_filename, content_type=content_type,
    )
    document = StudentDocument.objects.create(
        tenant=tenant, student=student, document_type=document_type, document=stored,
    )
    record_activity(
        tenant=tenant,
        actor=actor or user,
        action="student.document_added",
        resource_type="student",
        resource_id=str(student.id),
        metadata={"document_id": str(document.id)},
    )
    return document


def delete_student_document(*, user, tenant, student_document, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="students.document.manage")
    require_same_tenant(tenant=tenant, student=student_document.student)
    _require_campus_scope(membership=membership, campus_id=student_document.student.campus_id)

    if student_document.document is not None:
        delete_document(document=student_document.document)
    student_id = student_document.student_id
    document_id = student_document.id
    student_document.delete()
    record_activity(
        tenant=tenant,
        actor=actor or user,
        action="student.document_deleted",
        resource_type="student",
        resource_id=str(student_id),
        metadata={"document_id": str(document_id)},
    )