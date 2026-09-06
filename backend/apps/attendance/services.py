from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.academics.models import AcademicYear, EnrollmentStatus, StudentEnrollment, TeacherAssignment
from apps.activity.services import record_activity
from apps.tenancy.services import require_permission, require_same_tenant

from .models import AttendanceRecord, AttendanceSession, AttendanceSetup


def _resolve_academic_year(*, tenant, session_date):
    year = AcademicYear.objects.for_tenant(tenant).filter(starts_on__lte=session_date, ends_on__gte=session_date).first()
    if year is None:
        raise ValidationError("No academic year covers this date")
    return year


def _resolve_roster(*, tenant, class_group, session_date):
    """Historical enrollment, not a live Student field -- Student has no
    class_group of its own. Resolves against the AcademicYear whose date
    range actually contains session_date (not AcademicYear.is_current), so
    this works correctly for backfilled/corrected past dates too. Reuses
    the (tenant, class_group, status) index apps/academics/models.py:106-108
    already carries.
    """
    year = _resolve_academic_year(tenant=tenant, session_date=session_date)
    return list(
        StudentEnrollment.objects.for_tenant(tenant)
        .filter(class_group=class_group, academic_year=year, status=EnrollmentStatus.ACTIVE)
        .select_related("student")
    )


def _require_class_authorization(*, user, tenant, membership, class_group):
    if "attendance.any_class" in membership.role.permissions:
        return
    assigned = TeacherAssignment.objects.filter(tenant=tenant, teacher=user, class_group=class_group).exists()
    if not assigned:
        raise ValidationError("User is not assigned to this class")


def _is_instructional_day(*, tenant, session_date):
    setup = AttendanceSetup.objects.filter(tenant=tenant).first()
    instructional_days = setup.instructional_days if setup is not None else [1, 2, 3, 4, 5]
    return session_date.isoweekday() in instructional_days


@transaction.atomic
def open_attendance_session(*, user, tenant, class_group, session_date, force=False):
    membership = require_permission(user=user, tenant=tenant, permission="attendance.session.manage")
    require_same_tenant(tenant=tenant, class_group=class_group)
    _require_class_authorization(user=user, tenant=tenant, membership=membership, class_group=class_group)
    if not force and not _is_instructional_day(tenant=tenant, session_date=session_date):
        raise ValidationError("This date is not an instructional day; pass force=True to override")

    existing = AttendanceSession.objects.filter(tenant=tenant, class_group=class_group, session_date=session_date).first()
    if existing is not None:
        return existing, _resolve_roster(tenant=tenant, class_group=class_group, session_date=session_date)

    try:
        with transaction.atomic():
            session = AttendanceSession.objects.create(
                tenant=tenant, class_group=class_group, session_date=session_date, opened_by=user,
            )
    except IntegrityError as error:
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        sqlite_duplicate = str(cause) == (
            "UNIQUE constraint failed: attendance_attendancesession.tenant_id, "
            "attendance_attendancesession.class_group_id, attendance_attendancesession.session_date"
        )
        if constraint != "unique_attendance_session_per_class_per_day" and not sqlite_duplicate:
            raise
        session = AttendanceSession.objects.get(tenant=tenant, class_group=class_group, session_date=session_date)

    return session, _resolve_roster(tenant=tenant, class_group=class_group, session_date=session_date)


@transaction.atomic
def record_attendance_bulk(*, user, tenant, session, entries):
    """entries: iterable of {"student": Student, "status": AttendanceStatus, "remarks": str}.
    Locks the session row so two concurrent bulk submissions for the same
    session serialize rather than interleave -- the same lock-then-check-
    then-transition protocol apps/finance/services.py uses throughout.
    """
    membership = require_permission(user=user, tenant=tenant, permission="attendance.session.manage")
    require_same_tenant(tenant=tenant, session=session)
    _require_class_authorization(user=user, tenant=tenant, membership=membership, class_group=session.class_group)

    locked_session = AttendanceSession.objects.select_for_update().get(tenant=tenant, pk=session.pk)
    roster_student_ids = {
        enrollment.student_id
        for enrollment in _resolve_roster(tenant=tenant, class_group=locked_session.class_group, session_date=locked_session.session_date)
    }

    records = []
    for entry in entries:
        student = entry["student"]
        if student.id not in roster_student_ids:
            raise ValidationError(f"Student {student.id} is not enrolled in this class on {locked_session.session_date}")
        status = entry["status"]
        remarks = entry.get("remarks", "")
        record = AttendanceRecord.objects.filter(tenant=tenant, session=locked_session, student=student).first()
        if record is None:
            record = AttendanceRecord.objects.create(
                tenant=tenant, session=locked_session, student=student, status=status, remarks=remarks, recorded_by=user,
            )
        elif record.status != status or record.remarks != remarks:
            previous_status = record.status
            record.status = status
            record.remarks = remarks
            record.recorded_by = user
            record.save(update_fields=["status", "remarks", "recorded_by", "updated_at"])
            record_activity(
                tenant=tenant, actor=user, action="attendance.record.corrected",
                resource_type="attendance_record", resource_id=str(record.id),
                metadata={"previous_status": previous_status, "new_status": status},
            )
        records.append(record)

    locked_session.last_submitted_at = timezone.now()
    locked_session.save(update_fields=["last_submitted_at"])
    return records
