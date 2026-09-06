from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.activity.services import record_activity
from apps.students.models import Student
from apps.tenancy.models import Campus
from apps.tenancy.services import require_permission

from .models import AcademicYear, AcademicLevel, ClassGroup, Term, StudentEnrollment


@transaction.atomic
def enroll_student(*, user, tenant, student, academic_year, academic_level, class_group, campus, term=None):
    require_permission(user=user, tenant=tenant, permission="academics.students.enroll")
    def scoped(model, value):
        record = model.objects.for_tenant(tenant).filter(pk=value.pk).first()
        if record is None:
            raise ValidationError(f"{model.__name__} is not available in this tenant")
        return record

    student = scoped(Student, student)
    academic_year = scoped(AcademicYear, academic_year)
    academic_level = scoped(AcademicLevel, academic_level)
    class_group = scoped(ClassGroup, class_group)
    campus = scoped(Campus, campus)
    if term is not None:
        term = scoped(Term, term)
    candidate = StudentEnrollment(
        tenant=tenant, student=student, academic_year=academic_year,
        academic_level=academic_level, class_group=class_group, campus=campus, term=term,
    )
    candidate.clean()
    try:
        with transaction.atomic():
            candidate.save(force_insert=True)
            enrollment = candidate
    except IntegrityError as error:
        # Only translate the enrollment uniqueness violation, not unrelated failures.
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        sqlite_duplicate = str(cause) == (
            "UNIQUE constraint failed: academics_studentenrollment.tenant_id, "
            "academics_studentenrollment.student_id, academics_studentenrollment.academic_year_id"
        )
        if constraint != "unique_student_enrollment_per_year" and not sqlite_duplicate:
            raise
        raise ValidationError("Student already has an enrollment for this academic year") from error
    record_activity(
        tenant=tenant,
        actor=user,
        action="student.academically_enrolled",
        resource_type="student",
        resource_id=str(student.id),
        metadata={"enrollment_id": str(enrollment.id), "academic_year_id": str(academic_year.id)},
    )
    return enrollment
