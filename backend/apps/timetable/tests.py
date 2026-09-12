from datetime import date, time
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, Subject, TeacherAssignment, Term
from apps.activity.models import ActivityEvent
from apps.attendance.models import AttendanceSetup
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Period, TimetableEntry
from .services import (
    create_period,
    create_timetable_entry,
    delete_period,
    delete_timetable_entry,
    update_period,
    update_timetable_entry,
)


class TimetableFoundationTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="Coordinator",
            permissions=["timetable.manage", "timetable.setup.manage", "timetable.setup.view", "timetable.record.view"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.admin_role)

        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.other_campus = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")
        self.year = AcademicYear.objects.create(tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        self.term = Term.objects.create(tenant=self.school_a, academic_year=self.year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        self.level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_group = ClassGroup.objects.create(tenant=self.school_a, name="Grade 8 East", code="G8-E", academic_level=self.level, campus=self.campus)
        self.other_class_group = ClassGroup.objects.create(tenant=self.school_a, name="Grade 8 West", code="G8-W", academic_level=self.level, campus=self.campus)
        self.subject = Subject.objects.create(tenant=self.school_a, name="Math", code="MATH")

        self.teacher = User.objects.create_user(username="teacher", password="secret")
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.class_group, subject=self.subject)
        self.other_teacher = User.objects.create_user(username="other-teacher", password="secret")
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.other_teacher, class_group=self.other_class_group, subject=self.subject)

        self.period = Period.objects.create(tenant=self.school_a, name="Period 1", sequence=1, starts_at=time(8, 0), ends_at=time(8, 40))
        self.monday = 1

    def open_entry(self, **overrides):
        values = dict(
            user=self.admin, tenant=self.school_a, term=self.term, class_group=self.class_group,
            subject=self.subject, teacher=self.teacher, period=self.period, day_of_week=self.monday,
        )
        values.update(overrides)
        return create_timetable_entry(**values)


class PeriodTests(TimetableFoundationTests):
    def test_start_after_end_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "must start before it ends"):
            create_period(user=self.admin, tenant=self.school_a, name="Bad", sequence=2, starts_at=time(9, 0), ends_at=time(8, 0))

    def test_overlapping_period_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "overlaps"):
            create_period(user=self.admin, tenant=self.school_a, name="Overlap", sequence=2, starts_at=time(8, 20), ends_at=time(9, 0))

    def test_adjacent_period_is_allowed(self):
        period = create_period(user=self.admin, tenant=self.school_a, name="Period 2", sequence=2, starts_at=time(8, 40), ends_at=time(9, 20))
        self.assertIsNotNone(period.pk)

    def test_referenced_period_times_cannot_change(self):
        self.open_entry()
        with self.assertRaisesMessage(ValidationError, "already referenced"):
            update_period(user=self.admin, tenant=self.school_a, period=self.period, starts_at=time(8, 10))

    def test_unreferenced_period_times_can_change(self):
        updated = update_period(user=self.admin, tenant=self.school_a, period=self.period, starts_at=time(7, 55))
        self.assertEqual(updated.starts_at, time(7, 55))

    def test_name_and_sequence_remain_editable_once_referenced(self):
        self.open_entry()
        updated = update_period(user=self.admin, tenant=self.school_a, period=self.period, name="Renamed")
        self.assertEqual(updated.name, "Renamed")

    def test_referenced_period_cannot_be_deleted(self):
        self.open_entry()
        with self.assertRaisesMessage(ValidationError, "already referenced"):
            delete_period(user=self.admin, tenant=self.school_a, period=self.period)

    def test_unreferenced_period_can_be_deleted(self):
        delete_period(user=self.admin, tenant=self.school_a, period=self.period)
        self.assertEqual(Period.objects.filter(pk=self.period.pk).count(), 0)


class CreateTimetableEntryTests(TimetableFoundationTests):
    def test_creates_entry(self):
        entry = self.open_entry()
        self.assertEqual(entry.campus_id, self.campus.id)

    def test_non_instructional_day_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "not an instructional day"):
            self.open_entry(day_of_week=6)  # Saturday, default Mon-Fri

    def test_respects_configured_instructional_days(self):
        AttendanceSetup.objects.create(tenant=self.school_a, instructional_days=[6, 7])
        with self.assertRaisesMessage(ValidationError, "not an instructional day"):
            self.open_entry(day_of_week=self.monday)
        entry = self.open_entry(day_of_week=6)
        self.assertEqual(entry.day_of_week, 6)

    def test_non_teaching_period_is_rejected(self):
        break_period = Period.objects.create(tenant=self.school_a, name="Break", sequence=2, starts_at=time(9, 20), ends_at=time(9, 40), is_teaching_period=False)
        with self.assertRaisesMessage(ValidationError, "non-teaching period"):
            self.open_entry(period=break_period)

    def test_teacher_not_assigned_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "not assigned to this class and subject"):
            self.open_entry(teacher=self.other_teacher)

    def test_campus_scoped_coordinator_is_rejected_for_other_campus(self):
        Membership.objects.filter(tenant=self.school_a, user=self.admin).update(campus=self.other_campus)
        with self.assertRaisesMessage(ValidationError, "not authorized for this campus"):
            self.open_entry()

    def test_teacher_slot_collision_is_rejected(self):
        self.open_entry()
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.other_class_group, subject=self.subject)
        with self.assertRaisesMessage(ValidationError, "already teaching another class"):
            create_timetable_entry(
                user=self.admin, tenant=self.school_a, term=self.term, class_group=self.other_class_group,
                subject=self.subject, teacher=self.teacher, period=self.period, day_of_week=self.monday,
            )

    def test_room_slot_collision_is_rejected_same_campus(self):
        self.open_entry(room="Lab 1")
        with self.assertRaisesMessage(ValidationError, "already booked"):
            create_timetable_entry(
                user=self.admin, tenant=self.school_a, term=self.term, class_group=self.other_class_group,
                subject=self.subject, teacher=self.other_teacher, period=self.period, day_of_week=self.monday, room="lab 1",
            )

    def test_room_names_collide_case_and_whitespace_insensitively(self):
        self.open_entry(room="Lab 1")
        with self.assertRaisesMessage(ValidationError, "already booked"):
            create_timetable_entry(
                user=self.admin, tenant=self.school_a, term=self.term, class_group=self.other_class_group,
                subject=self.subject, teacher=self.other_teacher, period=self.period, day_of_week=self.monday, room="  LAB   1 ",
            )

    def test_blank_room_never_collides(self):
        self.open_entry(room="")
        entry2 = create_timetable_entry(
            user=self.admin, tenant=self.school_a, term=self.term, class_group=self.other_class_group,
            subject=self.subject, teacher=self.other_teacher, period=self.period, day_of_week=self.monday, room="",
        )
        self.assertIsNotNone(entry2.pk)

    def test_same_room_name_at_different_campus_does_not_collide(self):
        annex_class = ClassGroup.objects.create(tenant=self.school_a, name="Annex G8", code="G8-ANNEX", academic_level=self.level, campus=self.other_campus)
        annex_teacher = User.objects.create_user(username="annex-teacher", password="secret")
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=annex_teacher, class_group=annex_class, subject=self.subject)
        self.open_entry(room="Lab 1")
        entry2 = create_timetable_entry(
            user=self.admin, tenant=self.school_a, term=self.term, class_group=annex_class,
            subject=self.subject, teacher=annex_teacher, period=self.period, day_of_week=self.monday, room="Lab 1",
        )
        self.assertIsNotNone(entry2.pk)

    def test_exact_duplicate_resubmission_is_idempotent(self):
        first = self.open_entry(room="Lab 1")
        second = self.open_entry(room="lab 1")
        self.assertEqual(first.pk, second.pk)

    def test_mismatched_resubmission_for_the_same_slot_is_a_conflict(self):
        self.open_entry()
        with self.assertRaisesMessage(ValidationError, "already scheduled in this slot"):
            self.open_entry(room="Lab 2")

    def test_cross_tenant_validation(self):
        foreign_campus = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        foreign_year = AcademicYear.objects.create(tenant=self.school_b, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        foreign_term = Term.objects.create(tenant=self.school_b, academic_year=foreign_year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        foreign_level = AcademicLevel.objects.create(tenant=self.school_b, name="Grade 8", code="G8", sequence=8)
        foreign_class = ClassGroup.objects.create(tenant=self.school_b, name="Grade 8", code="G8", academic_level=foreign_level, campus=foreign_campus)
        with self.assertRaises(ValidationError):
            create_timetable_entry(
                user=self.admin, tenant=self.school_a, term=foreign_term, class_group=foreign_class,
                subject=self.subject, teacher=self.teacher, period=self.period, day_of_week=self.monday,
            )

    def test_unrelated_integrity_error_is_not_reported_as_a_timetable_conflict(self):
        with patch("apps.timetable.services.TimetableEntry.objects") as mocked_manager:
            from django.db import IntegrityError
            mocked_manager.create.side_effect = IntegrityError("some other constraint")
            mocked_manager.filter.return_value.first.return_value = None
            with self.assertRaises(IntegrityError):
                self.open_entry()


class UpdateAndDeleteTimetableEntryTests(TimetableFoundationTests):
    def test_update_changes_teacher_and_room_and_audits(self):
        entry = self.open_entry(room="Lab 1")
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.other_teacher, class_group=self.class_group, subject=self.subject)
        updated = update_timetable_entry(user=self.admin, tenant=self.school_a, entry=entry, teacher=self.other_teacher, room="Lab 2")
        self.assertEqual(updated.teacher_id, self.other_teacher.id)
        self.assertEqual(updated.room, "Lab 2")
        event = ActivityEvent.objects.get(action="timetable.entry.corrected")
        self.assertEqual(event.metadata["previous"]["teacher"], str(self.teacher.id))
        self.assertEqual(event.metadata["new"]["teacher"], str(self.other_teacher.id))

    def test_update_new_teacher_must_be_assigned(self):
        entry = self.open_entry()
        with self.assertRaisesMessage(ValidationError, "not assigned to this class and subject"):
            update_timetable_entry(user=self.admin, tenant=self.school_a, entry=entry, teacher=self.other_teacher)

    def test_noop_update_is_not_audited(self):
        entry = self.open_entry(room="Lab 1")
        update_timetable_entry(user=self.admin, tenant=self.school_a, entry=entry, room="lab 1")
        self.assertEqual(ActivityEvent.objects.count(), 0)

    def test_update_can_hit_a_fresh_collision(self):
        self.open_entry(room="Lab 1")
        entry2 = create_timetable_entry(
            user=self.admin, tenant=self.school_a, term=self.term, class_group=self.other_class_group,
            subject=self.subject, teacher=self.other_teacher, period=self.period, day_of_week=self.monday, room="Lab 2",
        )
        with self.assertRaisesMessage(ValidationError, "already booked"):
            update_timetable_entry(user=self.admin, tenant=self.school_a, entry=entry2, room="Lab 1")

    def test_delete_removes_entry_and_logs_activity(self):
        entry = self.open_entry()
        delete_timetable_entry(user=self.admin, tenant=self.school_a, entry=entry)
        self.assertEqual(TimetableEntry.objects.filter(pk=entry.pk).count(), 0)
        self.assertEqual(ActivityEvent.objects.filter(action="timetable.entry.deleted").count(), 1)

    def test_deleting_frees_the_period_for_editing(self):
        entry = self.open_entry()
        delete_timetable_entry(user=self.admin, tenant=self.school_a, entry=entry)
        updated = update_period(user=self.admin, tenant=self.school_a, period=self.period, starts_at=time(7, 30))
        self.assertEqual(updated.starts_at, time(7, 30))
