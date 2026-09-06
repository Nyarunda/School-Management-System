from django.core.exceptions import ValidationError

from apps.activity.services import record_activity
from apps.tenancy.services import require_same_tenant

from .models import Student, StudentDocument, StudentStatus


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


def add_student_document(*, student, document_type, file_name, actor=None):
    document = StudentDocument.objects.create(
        tenant=student.tenant,
        student=student,
        document_type=document_type,
        file_name=file_name,
    )
    record_activity(
        tenant=student.tenant,
        actor=actor,
        action="student.document_added",
        resource_type="student",
        resource_id=str(student.id),
        metadata={"document_id": str(document.id)},
    )
    return document