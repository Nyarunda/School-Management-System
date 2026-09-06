import uuid

from django.db import models

from apps.academics.models import AcademicLevel, ClassGroup, Subject, Term
from apps.students.models import Student
from apps.tenancy.models import TenantOwnedModel, User


class AssessmentType(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=30)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_assessment_type_code_per_tenant")]


class GradingScheme(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    academic_level = models.ForeignKey(AcademicLevel, on_delete=models.PROTECT, related_name="grading_schemes")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "name", "academic_level"], name="unique_grading_scheme_per_level")]


class GradingBand(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheme = models.ForeignKey(GradingScheme, on_delete=models.CASCADE, related_name="bands")
    grade_label = models.CharField(max_length=10)
    min_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    max_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    remark = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "scheme", "grade_label"], name="unique_grade_label_per_scheme")]


class AssessmentStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    APPROVED = "APPROVED", "Approved"
    PUBLISHED = "PUBLISHED", "Published"


class Assessment(TenantOwnedModel):
    """An exam/test for one class+subject+term. Opening one snapshots both
    the eligible roster (as AssessmentResult placeholders) and the grading
    scale (as AssessmentGradingBand rows) immediately -- a CAT can span
    weeks of marks entry, submission, approval, and publication, and neither
    who was expected to sit it nor how it was graded may silently change
    underneath an already-created assessment.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="assessments")
    assessment_type = models.ForeignKey(AssessmentType, on_delete=models.PROTECT, related_name="assessments")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="assessments")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="assessments")
    name = models.CharField(max_length=120)
    max_marks = models.DecimalField(max_digits=6, decimal_places=2)
    scheduled_date = models.DateField()
    grading_scheme = models.ForeignKey(GradingScheme, null=True, blank=True, on_delete=models.PROTECT, related_name="assessments")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=20, choices=AssessmentStatus.choices, default=AssessmentStatus.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    last_amended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "term", "class_group", "subject", "name"], name="unique_assessment_per_class_subject_term"),
        ]
        indexes = [models.Index(fields=["tenant", "class_group", "term"])]


class AssessmentGradingBand(TenantOwnedModel):
    """A frozen copy of the resolved GradingScheme's bands at creation time --
    editing GradingBand later must never change how an already-created
    assessment's scores are interpreted, same rationale as the roster
    snapshot on AssessmentResult.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="grading_bands")
    grade_label = models.CharField(max_length=10)
    min_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    max_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    remark = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "assessment", "grade_label"], name="unique_grade_label_per_assessment")]


class MarkStatus(models.TextChoices):
    NOT_MARKED = "NOT_MARKED", "Not marked"
    SCORED = "SCORED", "Scored"
    ABSENT = "ABSENT", "Absent"
    EXEMPT = "EXEMPT", "Exempt"


class AssessmentResult(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="results")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="assessment_results")
    mark_status = models.CharField(max_length=20, choices=MarkStatus.choices, default=MarkStatus.NOT_MARKED)
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=10, blank=True, default="")
    remarks = models.CharField(max_length=240, blank=True, default="")
    recorded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "assessment", "student"], name="unique_result_per_assessment_per_student")]
        indexes = [models.Index(fields=["tenant", "student", "assessment"])]
