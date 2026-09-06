import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.students.models import Student
from apps.tenancy.models import Campus, TenantOwnedModel, User


class AcademicSetup(TenantOwnedModel):
    calendar_name = models.CharField(max_length=120, default="School calendar")
    periods_per_year = models.PositiveSmallIntegerField(default=3)
    configuration = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="unique_academic_setup_per_tenant")]


class AcademicYear(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="unique_academic_year_per_tenant")]


class Term(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(max_length=80)
    starts_on = models.DateField()
    ends_on = models.DateField()
    sequence = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "academic_year", "sequence"], name="unique_term_sequence_per_year")]


class AcademicLevel(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=30)
    sequence = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_academic_level_code_per_tenant")]


class ClassGroup(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=30)
    academic_level = models.ForeignKey(AcademicLevel, on_delete=models.PROTECT, related_name="class_groups")
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT)
    stream = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_class_group_code_per_tenant")]


class Subject(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_subject_code_per_tenant")]


class TeacherAssignment(TenantOwnedModel):
    teacher = models.ForeignKey(User, on_delete=models.PROTECT, related_name="teaching_assignments")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name="teacher_assignments")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="teacher_assignments")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "teacher", "class_group", "subject"], name="unique_teacher_assignment_per_tenant")]


class EnrollmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    COMPLETED = "COMPLETED", "Completed"
    TRANSFERRED = "TRANSFERRED", "Transferred"


class StudentEnrollment(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="academic_enrollments")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="student_enrollments")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, null=True, blank=True, related_name="student_enrollments")
    academic_level = models.ForeignKey(AcademicLevel, on_delete=models.PROTECT)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=EnrollmentStatus.choices, default=EnrollmentStatus.ACTIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "student", "academic_year"],
                name="unique_student_enrollment_per_year",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "student", "academic_year"]),
            models.Index(fields=["tenant", "class_group", "status"]),
        ]

    def clean(self):
        related = [self.student, self.academic_year, self.academic_level, self.class_group, self.campus]
        if self.term is not None:
            related.append(self.term)
        if any(item.tenant_id != self.tenant_id for item in related):
            raise ValidationError("Academic enrollment records must belong to the same tenant")
        if self.term is not None and self.term.academic_year_id != self.academic_year_id:
            raise ValidationError("Term must belong to the selected academic year")
        if self.class_group.academic_level_id != self.academic_level_id:
            raise ValidationError("Class must belong to the selected academic level")
        if self.class_group.campus_id != self.campus_id:
            raise ValidationError("Class must belong to the selected campus")
