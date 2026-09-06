from django.core.exceptions import ValidationError
from django.db import transaction

from apps.activity.services import record_activity
from apps.tenancy.services import require_permission, require_same_tenant

from .models import StudentEnrollment


@transaction.atomic
def enroll_student(*, user, tenant, student, academic_year, academic_level, class_group, campus, term=None):
    require_permission(user=user, tenant=tenant, permission="academics.students.enroll")
    require_same_tenant(
        tenant=tenant,
        student=student,
        academic_year=academic_year,
        academic_level=academic_level,
        class_group=class_group,
        campus=campus,
    )
    if term is not None:
        require_same_tenant(tenant=tenant, term=term)
    enrollment = StudentEnrollment.objects.create(
        tenant=tenant,
        student=student,
        academic_year=academic_year,
        term=term,
        academic_level=academic_level,
        class_group=class_group,
        campus=campus,
    )
    record_activity(
        tenant=tenant,
        actor=user,
        action="student.academically_enrolled",
        resource_type="student",
        resource_id=str(student.id),
        metadata={"enrollment_id": str(enrollment.id), "academic_year_id": str(academic_year.id)},
    )
    return enrollment