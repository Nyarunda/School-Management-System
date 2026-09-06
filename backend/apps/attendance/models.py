import uuid

from django.db import models

from apps.academics.models import ClassGroup
from apps.students.models import Student
from apps.tenancy.models import TenantOwnedModel, User


def default_instructional_days():
    return [1, 2, 3, 4, 5]  # ISO weekday: Mon=1..Sun=7


class AttendanceSetup(TenantOwnedModel):
    """One per tenant. Which weekdays are instructional days -- session
    creation on a non-instructional day is rejected unless explicitly
    overridden. Deliberately narrow scope, matching AcademicSetup's own
    precedent (apps/academics/models.py) rather than a speculative rules
    engine nothing has asked for yet.
    """
    instructional_days = models.JSONField(default=default_instructional_days)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="unique_attendance_setup_per_tenant")]


class AttendanceStatus(models.TextChoices):
    PRESENT = "PRESENT", "Present"
    ABSENT = "ABSENT", "Absent"
    LATE = "LATE", "Late"
    EXCUSED = "EXCUSED", "Excused"
    SICK = "SICK", "Sick"
    SCHOOL_ACTIVITY = "SCHOOL_ACTIVITY", "School activity"


class AttendanceSession(TenantOwnedModel):
    """One register per class per calendar day -- daily attendance, not
    per-subject/period (there is no timetable/period data in this project
    to anchor a finer granularity). Corrections happen by resubmitting
    records against the same session, not by opening a new one.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="attendance_sessions")
    session_date = models.DateField()
    opened_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(auto_now_add=True)
    last_submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "class_group", "session_date"], name="unique_attendance_session_per_class_per_day"),
        ]
        indexes = [models.Index(fields=["tenant", "class_group", "session_date"])]


class AttendanceRecord(TenantOwnedModel):
    """Corrections are in-place updates, not an immutable reversal-record
    pattern like finance's AllocationReversal -- these aren't financial-
    grade, and academics.enroll_student doesn't use that pattern either.
    Auditability comes from record_activity (apps.activity.services)
    logging old/new status whenever a status actually changes, called from
    the service layer, not here.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name="records")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="attendance_records")
    status = models.CharField(max_length=20, choices=AttendanceStatus.choices)
    remarks = models.CharField(max_length=240, blank=True, default="")
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "session", "student"], name="unique_attendance_record_per_session_per_student"),
        ]
        indexes = [models.Index(fields=["tenant", "student", "session"])]
