from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.academics.models import TeacherAssignment
from apps.activity.services import record_activity
from apps.attendance.models import AttendanceSetup
from apps.tenancy.services import require_permission, require_same_tenant

from .models import Period, TimetableEntry

DEFAULT_INSTRUCTIONAL_DAYS = [1, 2, 3, 4, 5]

_SQLITE_CONFLICT_MESSAGES = {
    "unique_class_slot_per_term": (
        "UNIQUE constraint failed: timetable_timetableentry.tenant_id, timetable_timetableentry.term_id, "
        "timetable_timetableentry.class_group_id, timetable_timetableentry.day_of_week, timetable_timetableentry.period_id"
    ),
    "unique_teacher_slot_per_term": (
        "UNIQUE constraint failed: timetable_timetableentry.tenant_id, timetable_timetableentry.term_id, "
        "timetable_timetableentry.teacher_id, timetable_timetableentry.day_of_week, timetable_timetableentry.period_id"
    ),
    "unique_room_slot_per_term_campus": (
        "UNIQUE constraint failed: timetable_timetableentry.tenant_id, timetable_timetableentry.term_id, "
        "timetable_timetableentry.campus_id, timetable_timetableentry.room_key, timetable_timetableentry.day_of_week, "
        "timetable_timetableentry.period_id"
    ),
}

_CONFLICT_MESSAGES = {
    "unique_class_slot_per_term": "This class already has a lesson scheduled in this slot",
    "unique_teacher_slot_per_term": "This teacher is already teaching another class in this slot",
    "unique_room_slot_per_term_campus": "This room is already booked in this slot",
}


def _translate_conflict(error):
    cause = error.__cause__
    constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
    cause_str = str(cause)
    for name, message in _CONFLICT_MESSAGES.items():
        if constraint == name or cause_str == _SQLITE_CONFLICT_MESSAGES[name]:
            raise ValidationError(message) from error
    raise error


def _require_campus_scope(*, membership, class_group):
    if membership.campus_id is not None and class_group.campus_id != membership.campus_id:
        raise ValidationError("User is not authorized for this campus")


def _require_teacher_assigned(*, tenant, teacher, class_group, subject):
    assigned = TeacherAssignment.objects.filter(tenant=tenant, teacher=teacher, class_group=class_group, subject=subject).exists()
    if not assigned:
        raise ValidationError("Teacher is not assigned to this class and subject")


def _is_instructional_day(*, tenant, day_of_week):
    setup = AttendanceSetup.objects.filter(tenant=tenant).first()
    instructional_days = setup.instructional_days if setup is not None else DEFAULT_INSTRUCTIONAL_DAYS
    return day_of_week in instructional_days


def _normalize_room(room):
    return " ".join(room.strip().split()) if room else ""


def _periods_overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def create_period(*, user, tenant, name, sequence, starts_at, ends_at, is_teaching_period=True):
    require_permission(user=user, tenant=tenant, permission="timetable.setup.manage")
    if starts_at >= ends_at:
        raise ValidationError("Period must start before it ends")
    for existing in Period.objects.filter(tenant=tenant):
        if _periods_overlap(starts_at, ends_at, existing.starts_at, existing.ends_at):
            raise ValidationError(f"Period overlaps with '{existing.name}' ({existing.starts_at}-{existing.ends_at})")
    return Period.objects.create(
        tenant=tenant, name=name, sequence=sequence, starts_at=starts_at, ends_at=ends_at,
        is_teaching_period=is_teaching_period,
    )


def update_period(*, user, tenant, period, **fields):
    require_permission(user=user, tenant=tenant, permission="timetable.setup.manage")
    require_same_tenant(tenant=tenant, period=period)

    changing_locked_fields = {"starts_at", "ends_at", "is_teaching_period"} & fields.keys()
    if changing_locked_fields and TimetableEntry.objects.filter(tenant=tenant, period=period).exists():
        raise ValidationError("This period is already referenced by the timetable and cannot have its times changed")

    starts_at = fields.get("starts_at", period.starts_at)
    ends_at = fields.get("ends_at", period.ends_at)
    if "starts_at" in fields or "ends_at" in fields:
        if starts_at >= ends_at:
            raise ValidationError("Period must start before it ends")
        for existing in Period.objects.filter(tenant=tenant).exclude(pk=period.pk):
            if _periods_overlap(starts_at, ends_at, existing.starts_at, existing.ends_at):
                raise ValidationError(f"Period overlaps with '{existing.name}' ({existing.starts_at}-{existing.ends_at})")

    for field, value in fields.items():
        setattr(period, field, value)
    period.save(update_fields=list(fields.keys()))
    return period


def delete_period(*, user, tenant, period):
    require_permission(user=user, tenant=tenant, permission="timetable.setup.manage")
    require_same_tenant(tenant=tenant, period=period)
    if TimetableEntry.objects.filter(tenant=tenant, period=period).exists():
        raise ValidationError("This period is already referenced by the timetable and cannot be deleted")
    period.delete()


@transaction.atomic
def create_timetable_entry(*, user, tenant, term, class_group, subject, teacher, period, day_of_week, room=""):
    membership = require_permission(user=user, tenant=tenant, permission="timetable.manage")
    require_same_tenant(tenant=tenant, term=term, class_group=class_group, subject=subject, period=period)
    _require_campus_scope(membership=membership, class_group=class_group)
    _require_teacher_assigned(tenant=tenant, teacher=teacher, class_group=class_group, subject=subject)
    if not _is_instructional_day(tenant=tenant, day_of_week=day_of_week):
        raise ValidationError("This day is not an instructional day for this tenant")
    if not period.is_teaching_period:
        raise ValidationError("Cannot schedule a lesson during a non-teaching period")

    room = _normalize_room(room)
    room_key = room.upper()
    existing = TimetableEntry.objects.filter(tenant=tenant, term=term, class_group=class_group, day_of_week=day_of_week, period=period).first()
    if existing is not None:
        if existing.subject_id == subject.id and existing.teacher_id == teacher.id and existing.room_key == room_key:
            return existing
        raise ValidationError("A different lesson is already scheduled in this slot; use update instead")

    try:
        with transaction.atomic():
            entry = TimetableEntry.objects.create(
                tenant=tenant, term=term, class_group=class_group, campus=class_group.campus, subject=subject,
                teacher=teacher, period=period, day_of_week=day_of_week, room=room, created_by=user,
            )
    except IntegrityError as error:
        _translate_conflict(error)  # always raises
    return entry


@transaction.atomic
def update_timetable_entry(*, user, tenant, entry, teacher=None, room=None):
    membership = require_permission(user=user, tenant=tenant, permission="timetable.manage")
    require_same_tenant(tenant=tenant, entry=entry)
    locked_entry = TimetableEntry.objects.select_for_update().get(tenant=tenant, pk=entry.pk)
    _require_campus_scope(membership=membership, class_group=locked_entry.class_group)

    if teacher is not None:
        _require_teacher_assigned(tenant=tenant, teacher=teacher, class_group=locked_entry.class_group, subject=locked_entry.subject)

    previous = {"teacher": str(locked_entry.teacher_id), "room": locked_entry.room}
    new_teacher = teacher if teacher is not None else locked_entry.teacher
    new_room = _normalize_room(room) if room is not None else locked_entry.room
    changed = new_teacher.id != locked_entry.teacher_id or new_room.upper() != locked_entry.room_key

    if not changed:
        return locked_entry

    locked_entry.teacher = new_teacher
    locked_entry.room = new_room
    try:
        locked_entry.save(update_fields=["teacher", "room", "room_key", "updated_at"])
    except IntegrityError as error:
        _translate_conflict(error)
        raise

    record_activity(
        tenant=tenant, actor=user, action="timetable.entry.corrected",
        resource_type="timetable_entry", resource_id=str(locked_entry.id),
        metadata={"previous": previous, "new": {"teacher": str(locked_entry.teacher_id), "room": locked_entry.room}},
    )
    return locked_entry


@transaction.atomic
def delete_timetable_entry(*, user, tenant, entry):
    membership = require_permission(user=user, tenant=tenant, permission="timetable.manage")
    require_same_tenant(tenant=tenant, entry=entry)
    locked_entry = TimetableEntry.objects.select_for_update().get(tenant=tenant, pk=entry.pk)
    _require_campus_scope(membership=membership, class_group=locked_entry.class_group)
    entry_id = str(locked_entry.id)
    locked_entry.delete()
    record_activity(
        tenant=tenant, actor=user, action="timetable.entry.deleted",
        resource_type="timetable_entry", resource_id=entry_id,
    )


def resolve_class_schedule(*, tenant, term, class_group):
    return list(
        TimetableEntry.objects.filter(tenant=tenant, term=term, class_group=class_group)
        .select_related("subject", "teacher", "period")
        .order_by("day_of_week", "period__sequence")
    )


def resolve_teacher_schedule(*, tenant, term, teacher):
    return list(
        TimetableEntry.objects.filter(tenant=tenant, term=term, teacher=teacher)
        .select_related("subject", "class_group", "period")
        .order_by("day_of_week", "period__sequence")
    )
