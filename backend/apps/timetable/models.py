import uuid

from django.db import models

from apps.academics.models import ClassGroup, Subject, Term
from apps.tenancy.models import Campus, TenantOwnedModel, User


class Period(TenantOwnedModel):
    """Tenant-wide bell schedule. Once any TimetableEntry references a
    period, its starts_at/ends_at/is_teaching_period become immutable (see
    apps.timetable.services.update_period) -- checked live against actual
    TimetableEntry references, not a stored flag that could go stale.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=40)
    sequence = models.PositiveSmallIntegerField()
    starts_at = models.TimeField()
    ends_at = models.TimeField()
    is_teaching_period = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "sequence"], name="unique_period_sequence_per_tenant"),
            models.CheckConstraint(condition=models.Q(starts_at__lt=models.F("ends_at")), name="period_start_before_end"),
        ]


class TimetableEntry(TenantOwnedModel):
    """A single class+day+period lesson slot. Corrections are in-place
    updates to teacher/room only (day/period/class/subject/term define the
    slot's identity) -- no effective-dated history, matching Attendance's
    correction model rather than Assessments' locked lifecycle, since a
    timetable is continuously-live operational configuration.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="timetable_entries")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="timetable_entries")
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="timetable_entries")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="timetable_entries")
    teacher = models.ForeignKey(User, on_delete=models.PROTECT, related_name="timetable_entries")
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="timetable_entries")
    day_of_week = models.PositiveSmallIntegerField()  # ISO weekday: Mon=1..Sun=7
    room = models.CharField(max_length=60, blank=True, default="")
    room_key = models.CharField(max_length=60, blank=True, default="", editable=False)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.room_key = " ".join(self.room.strip().upper().split()) if self.room else ""
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "term", "class_group", "day_of_week", "period"], name="unique_class_slot_per_term"),
            models.UniqueConstraint(fields=["tenant", "term", "teacher", "day_of_week", "period"], name="unique_teacher_slot_per_term"),
            models.UniqueConstraint(
                fields=["tenant", "term", "campus", "room_key", "day_of_week", "period"],
                condition=models.Q(room_key__gt=""), name="unique_room_slot_per_term_campus",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "term", "class_group"]),
            models.Index(fields=["tenant", "term", "teacher"]),
        ]
