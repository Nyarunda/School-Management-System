from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.academics.models import AcademicYear, EnrollmentStatus, StudentEnrollment, TeacherAssignment
from apps.activity.services import record_activity
from apps.tenancy.services import require_permission, require_same_tenant

from .models import (
    Assessment,
    AssessmentGradingBand,
    AssessmentResult,
    AssessmentStatus,
    GradingBand,
    GradingScheme,
    MarkStatus,
)


def _resolve_roster(*, tenant, class_group, academic_year):
    """Same shape as apps.attendance.services._resolve_roster, used once at
    assessment-creation time only -- the result is materialized into
    AssessmentResult rows and never re-resolved afterward.
    """
    return list(
        StudentEnrollment.objects.for_tenant(tenant)
        .filter(class_group=class_group, academic_year=academic_year, status=EnrollmentStatus.ACTIVE)
        .select_related("student")
    )


def _require_subject_class_authorization(*, user, tenant, membership, class_group, subject):
    """Campus scope first (same fix as apps.attendance.services 6.1), then
    subject-scoped TeacherAssignment -- unlike Attendance's any-subject
    rule, an Assessment is inherently tied to one subject.
    """
    if membership.campus_id is not None and class_group.campus_id != membership.campus_id:
        raise ValidationError("User is not authorized for this campus")
    if "assessment.any_class" in membership.role.permissions:
        return
    assigned = TeacherAssignment.objects.filter(tenant=tenant, teacher=user, class_group=class_group, subject=subject).exists()
    if not assigned:
        raise ValidationError("User is not assigned to this class and subject")


def _resolve_active_grading_scheme(*, tenant, academic_level):
    """unique_active_grading_scheme_per_level (a conditional unique
    constraint on GradingScheme) guarantees at most one row can ever match,
    so a plain get()/DoesNotExist is sufficient -- no ambiguity to detect
    here, unlike the .first()-over-a-list approach this replaced.
    """
    try:
        return GradingScheme.objects.get(tenant=tenant, academic_level=academic_level, is_active=True)
    except GradingScheme.DoesNotExist:
        return None


def _resolve_grade(*, score, max_marks, grading_bands):
    if not grading_bands or max_marks == 0:
        return ""
    percentage = (score / max_marks) * Decimal("100")
    for band in grading_bands:
        if band.min_percentage <= percentage <= band.max_percentage:
            return band.grade_label
    return ""


def add_grading_band(*, user, tenant, scheme, grade_label, min_percentage, max_percentage, remark=""):
    require_permission(user=user, tenant=tenant, permission="assessment.setup.manage")
    require_same_tenant(tenant=tenant, scheme=scheme)
    if min_percentage > max_percentage:
        raise ValidationError("min_percentage cannot exceed max_percentage")
    overlapping = GradingBand.objects.filter(tenant=tenant, scheme=scheme).filter(
        min_percentage__lte=max_percentage, max_percentage__gte=min_percentage,
    ).exists()
    if overlapping:
        raise ValidationError("This band overlaps an existing band on the same scheme")
    return GradingBand.objects.create(
        tenant=tenant, scheme=scheme, grade_label=grade_label,
        min_percentage=min_percentage, max_percentage=max_percentage, remark=remark,
    )


def _require_no_unmarked_results(*, tenant, assessment):
    if AssessmentResult.objects.filter(tenant=tenant, assessment=assessment, mark_status=MarkStatus.NOT_MARKED).exists():
        raise ValidationError("All candidates must be marked (scored, absent, or exempt) first")


@transaction.atomic
def create_assessment(*, user, tenant, term, class_group, subject, assessment_type, name, max_marks, scheduled_date):
    membership = require_permission(user=user, tenant=tenant, permission="assessment.manage")
    require_same_tenant(tenant=tenant, term=term, class_group=class_group, subject=subject, assessment_type=assessment_type)
    _require_subject_class_authorization(user=user, tenant=tenant, membership=membership, class_group=class_group, subject=subject)
    if max_marks <= 0:
        raise ValidationError("max_marks must be greater than zero")

    existing = Assessment.objects.filter(tenant=tenant, term=term, class_group=class_group, subject=subject, name=name).first()
    if existing is not None:
        return existing, list(AssessmentResult.objects.filter(tenant=tenant, assessment=existing).select_related("student"))

    roster = _resolve_roster(tenant=tenant, class_group=class_group, academic_year=term.academic_year)
    grading_scheme = _resolve_active_grading_scheme(tenant=tenant, academic_level=class_group.academic_level)

    try:
        with transaction.atomic():
            assessment = Assessment.objects.create(
                tenant=tenant, term=term, class_group=class_group, subject=subject, assessment_type=assessment_type,
                name=name, max_marks=max_marks, scheduled_date=scheduled_date, grading_scheme=grading_scheme, created_by=user,
            )
            if grading_scheme is not None:
                AssessmentGradingBand.objects.bulk_create([
                    AssessmentGradingBand(
                        tenant=tenant, assessment=assessment, grade_label=band.grade_label,
                        min_percentage=band.min_percentage, max_percentage=band.max_percentage, remark=band.remark,
                    )
                    for band in grading_scheme.bands.all()
                ])
            AssessmentResult.objects.bulk_create([
                AssessmentResult(tenant=tenant, assessment=assessment, student=enrollment.student, mark_status=MarkStatus.NOT_MARKED)
                for enrollment in roster
            ])
    except IntegrityError as error:
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        sqlite_duplicate = str(cause) == (
            "UNIQUE constraint failed: assessments_assessment.tenant_id, assessments_assessment.term_id, "
            "assessments_assessment.class_group_id, assessments_assessment.subject_id, assessments_assessment.name"
        )
        if constraint != "unique_assessment_per_class_subject_term" and not sqlite_duplicate:
            raise
        assessment = Assessment.objects.get(tenant=tenant, term=term, class_group=class_group, subject=subject, name=name)
        return assessment, list(AssessmentResult.objects.filter(tenant=tenant, assessment=assessment).select_related("student"))

    return assessment, list(AssessmentResult.objects.filter(tenant=tenant, assessment=assessment).select_related("student"))


@transaction.atomic
def record_assessment_marks(*, user, tenant, assessment, entries):
    """Marks are only editable in DRAFT (assessment.marks.manage) or
    PUBLISHED (assessment.result.amend) -- SUBMITTED/APPROVED are locked
    outright, so "submitted" and "approved" carry real meaning (a teacher
    cannot quietly change a mark an approver just signed off on). Every
    persisted field (mark_status, score, grade, remarks) is compared for
    the audit log, not just mark_status.
    """
    require_same_tenant(tenant=tenant, assessment=assessment)
    locked_assessment = Assessment.objects.select_for_update().get(tenant=tenant, pk=assessment.pk)

    if locked_assessment.status == AssessmentStatus.PUBLISHED:
        permission = "assessment.result.amend"
    elif locked_assessment.status == AssessmentStatus.DRAFT:
        permission = "assessment.marks.manage"
    else:
        raise ValidationError(f"Marks cannot be edited while the assessment is {locked_assessment.status}")

    membership = require_permission(user=user, tenant=tenant, permission=permission)
    _require_subject_class_authorization(
        user=user, tenant=tenant, membership=membership,
        class_group=locked_assessment.class_group, subject=locked_assessment.subject,
    )

    grading_bands = list(locked_assessment.grading_bands.all())
    records = []
    for entry in entries:
        student = entry["student"]
        mark_status = entry["mark_status"]
        score = entry.get("score")
        remarks = entry.get("remarks", "")

        if mark_status == MarkStatus.SCORED:
            if score is None or not (0 <= score <= locked_assessment.max_marks):
                raise ValidationError(f"score must be between 0 and {locked_assessment.max_marks} when scored")
            grade = _resolve_grade(score=score, max_marks=locked_assessment.max_marks, grading_bands=grading_bands)
        else:
            if score is not None:
                raise ValidationError("score must be empty unless mark_status is SCORED")
            grade = ""

        result = AssessmentResult.objects.filter(tenant=tenant, assessment=locked_assessment, student=student).first()
        if result is None:
            raise ValidationError(f"Student {student.id} is not on this assessment's roster")

        was_marked = result.mark_status != MarkStatus.NOT_MARKED
        new_values = {"mark_status": mark_status, "score": score, "grade": grade, "remarks": remarks}
        changed = any(getattr(result, field) != value for field, value in new_values.items())
        if changed:
            previous = {"mark_status": result.mark_status, "score": result.score, "grade": result.grade, "remarks": result.remarks}
            for field, value in new_values.items():
                setattr(result, field, value)
            result.recorded_by = user
            result.save(update_fields=["mark_status", "score", "grade", "remarks", "recorded_by", "updated_at"])
            if was_marked:
                # JSONField has no Decimal support -- stringify score for the
                # audit trail; the model itself keeps the real Decimal.
                record_activity(
                    tenant=tenant, actor=user, action="assessment.result.corrected",
                    resource_type="assessment_result", resource_id=str(result.id),
                    metadata={
                        "previous": {**previous, "score": str(previous["score"]) if previous["score"] is not None else None},
                        "new": {**new_values, "score": str(new_values["score"]) if new_values["score"] is not None else None},
                    },
                )
                if locked_assessment.status == AssessmentStatus.PUBLISHED:
                    locked_assessment.last_amended_at = timezone.now()
                    locked_assessment.save(update_fields=["last_amended_at"])
        records.append(result)

    return records


@transaction.atomic
def submit_assessment_for_approval(*, user, tenant, assessment):
    membership = require_permission(user=user, tenant=tenant, permission="assessment.marks.manage")
    require_same_tenant(tenant=tenant, assessment=assessment)
    locked_assessment = Assessment.objects.select_for_update().get(tenant=tenant, pk=assessment.pk)
    _require_subject_class_authorization(
        user=user, tenant=tenant, membership=membership,
        class_group=locked_assessment.class_group, subject=locked_assessment.subject,
    )
    if locked_assessment.status != AssessmentStatus.DRAFT:
        raise ValidationError("Only draft assessments can be submitted for approval")
    _require_no_unmarked_results(tenant=tenant, assessment=locked_assessment)

    locked_assessment.status = AssessmentStatus.SUBMITTED
    locked_assessment.submitted_at = timezone.now()
    locked_assessment.save(update_fields=["status", "submitted_at"])
    record_activity(
        tenant=tenant, actor=user, action="assessment.submitted",
        resource_type="assessment", resource_id=str(locked_assessment.id),
    )
    return locked_assessment


@transaction.atomic
def reject_assessment_submission(*, user, tenant, assessment, reason):
    require_permission(user=user, tenant=tenant, permission="assessment.approve")
    require_same_tenant(tenant=tenant, assessment=assessment)
    locked_assessment = Assessment.objects.select_for_update().get(tenant=tenant, pk=assessment.pk)
    if locked_assessment.status != AssessmentStatus.SUBMITTED:
        raise ValidationError("Only submitted assessments can be rejected")

    locked_assessment.status = AssessmentStatus.DRAFT
    locked_assessment.submitted_at = None
    locked_assessment.save(update_fields=["status", "submitted_at"])
    record_activity(
        tenant=tenant, actor=user, action="assessment.rejected",
        resource_type="assessment", resource_id=str(locked_assessment.id), metadata={"reason": reason},
    )
    return locked_assessment


@transaction.atomic
def approve_assessment(*, user, tenant, assessment):
    require_permission(user=user, tenant=tenant, permission="assessment.approve")
    require_same_tenant(tenant=tenant, assessment=assessment)
    locked_assessment = Assessment.objects.select_for_update().get(tenant=tenant, pk=assessment.pk)
    if locked_assessment.status != AssessmentStatus.SUBMITTED:
        raise ValidationError("Only submitted assessments can be approved")
    _require_no_unmarked_results(tenant=tenant, assessment=locked_assessment)

    locked_assessment.status = AssessmentStatus.APPROVED
    locked_assessment.approved_at = timezone.now()
    locked_assessment.save(update_fields=["status", "approved_at"])
    record_activity(
        tenant=tenant, actor=user, action="assessment.approved",
        resource_type="assessment", resource_id=str(locked_assessment.id),
    )
    return locked_assessment


@transaction.atomic
def reopen_approved_assessment(*, user, tenant, assessment, reason):
    require_permission(user=user, tenant=tenant, permission="assessment.approve")
    require_same_tenant(tenant=tenant, assessment=assessment)
    locked_assessment = Assessment.objects.select_for_update().get(tenant=tenant, pk=assessment.pk)
    if locked_assessment.status != AssessmentStatus.APPROVED:
        raise ValidationError("Only approved assessments can be reopened")

    locked_assessment.status = AssessmentStatus.DRAFT
    locked_assessment.approved_at = None
    locked_assessment.submitted_at = None
    locked_assessment.save(update_fields=["status", "approved_at", "submitted_at"])
    record_activity(
        tenant=tenant, actor=user, action="assessment.reopened",
        resource_type="assessment", resource_id=str(locked_assessment.id), metadata={"reason": reason},
    )
    return locked_assessment


@transaction.atomic
def publish_assessment(*, user, tenant, assessment):
    require_permission(user=user, tenant=tenant, permission="assessment.publish")
    require_same_tenant(tenant=tenant, assessment=assessment)
    locked_assessment = Assessment.objects.select_for_update().get(tenant=tenant, pk=assessment.pk)
    if locked_assessment.status != AssessmentStatus.APPROVED:
        raise ValidationError("Only approved assessments can be published")
    _require_no_unmarked_results(tenant=tenant, assessment=locked_assessment)

    locked_assessment.status = AssessmentStatus.PUBLISHED
    locked_assessment.published_at = timezone.now()
    locked_assessment.save(update_fields=["status", "published_at"])
    record_activity(
        tenant=tenant, actor=user, action="assessment.published",
        resource_type="assessment", resource_id=str(locked_assessment.id),
    )
    return locked_assessment
