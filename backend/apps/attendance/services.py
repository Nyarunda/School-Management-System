from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.academics.models import AcademicYear, EnrollmentStatus, StudentEnrollment, TeacherAssignment
from apps.activity.services import record_activity
from apps.tenancy.services import require_permission, require_same_tenant

from .models import AttendanceRecord, AttendanceSession, AttendanceSessionStatus, AttendanceSetup, AttendanceStatus


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
    already carries. Called once, at session-creation time only -- the
    result is materialized into AttendanceRecord rows and never re-resolved
    afterward (see open_attendance_session).
    """
    year = _resolve_academic_year(tenant=tenant, session_date=session_date)
    return list(
        StudentEnrollment.objects.for_tenant(tenant)
        .filter(class_group=class_group, academic_year=year, status=EnrollmentStatus.ACTIVE)
        .select_related("student")
    )


def _require_class_authorization(*, user, tenant, membership, class_group):
    """Campus scope is checked first, before the any_class escape hatch, so
    a user scoped to one campus (Membership.campus) can never reach a
    class in another campus -- any_class only bypasses the TeacherAssignment
    requirement, never tenant/campus scope.
    """
    if membership.campus_id is not None and class_group.campus_id != membership.campus_id:
        raise ValidationError("User is not authorized for this campus")
    if "attendance.any_class" in membership.role.permissions:
        return
    assigned = TeacherAssignment.objects.filter(tenant=tenant, teacher=user, class_group=class_group).exists()
    if not assigned:
        raise ValidationError("User is not assigned to this class")


def _is_instructional_day(*, tenant, session_date):
    setup = AttendanceSetup.objects.filter(tenant=tenant).first()
    instructional_days = setup.instructional_days if setup is not None else [1, 2, 3, 4, 5]
    return session_date.isoweekday() in instructional_days


def _session_records(*, tenant, session):
    return list(AttendanceRecord.objects.filter(tenant=tenant, session=session).select_related("student"))


@transaction.atomic
def open_attendance_session(*, user, tenant, class_group, session_date, force=False):
    """Opening a session snapshots the roster immediately: one AttendanceRecord
    per active enrollment, status=NOT_MARKED, recorded_by=None. This is a
    deliberate departure from resolving the roster live on every read -- once
    a session exists, a later enrollment correction must not silently change
    who appears to have been expected in that day's register.
    """
    membership = require_permission(user=user, tenant=tenant, permission="attendance.session.manage")
    require_same_tenant(tenant=tenant, class_group=class_group)
    _require_class_authorization(user=user, tenant=tenant, membership=membership, class_group=class_group)

    calendar_overridden = False
    if not _is_instructional_day(tenant=tenant, session_date=session_date):
        if not force:
            raise ValidationError("This date is not an instructional day; pass force=True to override")
        # force alone is not enough to bypass calendar policy -- an ordinary
        # attendance.session.manage teacher cannot self-authorize a
        # non-instructional-day register; only an explicit, separate
        # permission can.
        require_permission(user=user, tenant=tenant, permission="attendance.session.override_calendar")
        calendar_overridden = True

    existing = AttendanceSession.objects.filter(tenant=tenant, class_group=class_group, session_date=session_date).first()
    if existing is not None:
        return existing, _session_records(tenant=tenant, session=existing)

    roster = _resolve_roster(tenant=tenant, class_group=class_group, session_date=session_date)

    try:
        with transaction.atomic():
            session = AttendanceSession.objects.create(
                tenant=tenant, class_group=class_group, session_date=session_date, opened_by=user,
            )
            AttendanceRecord.objects.bulk_create([
                AttendanceRecord(tenant=tenant, session=session, student=enrollment.student, status=AttendanceStatus.NOT_MARKED)
                for enrollment in roster
            ])
    except IntegrityError as error:
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        sqlite_duplicate = str(cause) == (
            "UNIQUE constraint failed: attendance_attendancesession.tenant_id, "
            "attendance_attendancesession.class_group_id, attendance_attendancesession.session_date"
        )
        if constraint != "unique_attendance_session_per_class_per_day" and not sqlite_duplicate:
            raise
        # The other transaction that won the race committed its session and
        # roster snapshot together (same atomic block), so it's safe to just
        # read what it left behind rather than materializing again.
        session = AttendanceSession.objects.get(tenant=tenant, class_group=class_group, session_date=session_date)
        return session, _session_records(tenant=tenant, session=session)

    if calendar_overridden:
        record_activity(
            tenant=tenant, actor=user, action="attendance.session.calendar_overridden",
            resource_type="attendance_session", resource_id=str(session.id),
        )

    return session, _session_records(tenant=tenant, session=session)


@transaction.atomic
def record_attendance_bulk(*, user, tenant, session, entries):
    """entries: iterable of {"student": Student, "status": AttendanceStatus, "remarks": str}.
    Locks the session row so two concurrent bulk submissions for the same
    session serialize rather than interleave -- the same lock-then-check-
    then-transition protocol apps/finance/services.py uses throughout.
    Every roster student already has a placeholder AttendanceRecord from
    open_attendance_session, so "not on the roster" is now simply "no
    record exists for this student in this session" rather than a live
    re-resolution. Corrections remain allowed regardless of the session's
    OPEN/SUBMITTED status (same-day operational fixes, not a multi-step
    approval pipeline) -- they are always audited.
    """
    membership = require_permission(user=user, tenant=tenant, permission="attendance.session.manage")
    require_same_tenant(tenant=tenant, session=session)
    _require_class_authorization(user=user, tenant=tenant, membership=membership, class_group=session.class_group)

    locked_session = AttendanceSession.objects.select_for_update().get(tenant=tenant, pk=session.pk)

    records = []
    for entry in entries:
        student = entry["student"]
        status = entry["status"]
        remarks = entry.get("remarks", "")
        record = AttendanceRecord.objects.filter(tenant=tenant, session=locked_session, student=student).first()
        if record is None:
            raise ValidationError(f"Student {student.id} is not enrolled in this class on {locked_session.session_date}")
        was_marked = record.status != AttendanceStatus.NOT_MARKED
        if record.status != status or record.remarks != remarks:
            previous = {"status": record.status, "remarks": record.remarks}
            record.status = status
            record.remarks = remarks
            record.recorded_by = user
            record.save(update_fields=["status", "remarks", "recorded_by", "updated_at"])
            if was_marked:
                record_activity(
                    tenant=tenant, actor=user, action="attendance.record.corrected",
                    resource_type="attendance_record", resource_id=str(record.id),
                    metadata={"previous": previous, "new": {"status": status, "remarks": remarks}},
                )
        records.append(record)

    locked_session.last_submitted_at = timezone.now()
    locked_session.save(update_fields=["last_submitted_at"])
    return records


@transaction.atomic
def submit_attendance_session(*, user, tenant, session):
    """Finalizes the register for the day: requires every roster student to
    have moved off NOT_MARKED first (the completeness gate), then marks the
    session SUBMITTED so "which classes haven't submitted today" becomes a
    real, answerable query. Corrections after submission remain possible via
    record_attendance_bulk -- this only gates the finalization step itself.
    """
    membership = require_permission(user=user, tenant=tenant, permission="attendance.session.manage")
    require_same_tenant(tenant=tenant, session=session)
    _require_class_authorization(user=user, tenant=tenant, membership=membership, class_group=session.class_group)

    locked_session = AttendanceSession.objects.select_for_update().get(tenant=tenant, pk=session.pk)
    if locked_session.status != AttendanceSessionStatus.OPEN:
        raise ValidationError("Session has already been submitted")
    if AttendanceRecord.objects.filter(tenant=tenant, session=locked_session, status=AttendanceStatus.NOT_MARKED).exists():
        raise ValidationError("All roster students must be marked before submission")

    locked_session.status = AttendanceSessionStatus.SUBMITTED
    locked_session.submitted_by = user
    locked_session.submitted_at = timezone.now()
    locked_session.save(update_fields=["status", "submitted_by", "submitted_at"])
    record_activity(
        tenant=tenant, actor=user, action="attendance.session.submitted",
        resource_type="attendance_session", resource_id=str(locked_session.id),
    )
    return locked_session
