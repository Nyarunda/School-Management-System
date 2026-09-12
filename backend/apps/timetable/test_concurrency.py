"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, Subject, TeacherAssignment, Term
from apps.activity.models import ActivityEvent
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Period, TimetableEntry
from .services import create_timetable_entry, update_timetable_entry


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class TimetableConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.tenant, name="Coordinator",
            permissions=["timetable.manage", "timetable.setup.manage", "timetable.record.view"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=self.role)
        self.campus = Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")
        self.year = AcademicYear.objects.create(tenant=self.tenant, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        self.term = Term.objects.create(tenant=self.tenant, academic_year=self.year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        self.level = AcademicLevel.objects.create(tenant=self.tenant, name="Grade 8", code="G8", sequence=8)
        self.class_a = ClassGroup.objects.create(tenant=self.tenant, name="Grade 8A", code="G8A", academic_level=self.level, campus=self.campus)
        self.class_b = ClassGroup.objects.create(tenant=self.tenant, name="Grade 8B", code="G8B", academic_level=self.level, campus=self.campus)
        self.subject = Subject.objects.create(tenant=self.tenant, name="Math", code="MATH")
        self.teacher_a = User.objects.create_user(username="teacher-a", password="secret")
        self.teacher_b = User.objects.create_user(username="teacher-b", password="secret")
        TeacherAssignment.objects.create(tenant=self.tenant, teacher=self.teacher_a, class_group=self.class_a, subject=self.subject)
        TeacherAssignment.objects.create(tenant=self.tenant, teacher=self.teacher_a, class_group=self.class_b, subject=self.subject)
        TeacherAssignment.objects.create(tenant=self.tenant, teacher=self.teacher_b, class_group=self.class_b, subject=self.subject)
        self.period = Period.objects.create(tenant=self.tenant, name="Period 1", sequence=1, starts_at=time(8, 0), ends_at=time(8, 40))
        self.monday = 1

    def _attempt_create(self, *, class_group, teacher, room, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")

            def synchronize_insert(execute, sql, params, many, context):
                if sql.startswith('INSERT INTO "timetable_timetableentry"'):
                    barrier.wait(timeout=6)
                return execute(sql, params, many, context)

            with connection.execute_wrapper(synchronize_insert):
                try:
                    create_timetable_entry(
                        user=self.admin, tenant=self.tenant, term=self.term, class_group=class_group,
                        subject=self.subject, teacher=teacher, period=self.period, day_of_week=self.monday, room=room,
                    )
                    return "created"
                except ValidationError as error:
                    return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_class_slot_creation_resolves_to_exactly_one_entry(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_create, class_group=self.class_a, teacher=self.teacher_a, room="Lab 1", barrier=barrier),
                pool.submit(self._attempt_create, class_group=self.class_a, teacher=self.teacher_a, room="Lab 2", barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(TimetableEntry.objects.filter(class_group=self.class_a, day_of_week=self.monday, period=self.period).count(), 1)
        self.assertEqual(sorted(outcomes), sorted(["created", "This class already has a lesson scheduled in this slot"]))

    def test_competing_teacher_slot_creation_resolves_to_exactly_one_entry(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_create, class_group=self.class_a, teacher=self.teacher_a, room="", barrier=barrier),
                pool.submit(self._attempt_create, class_group=self.class_b, teacher=self.teacher_a, room="", barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(TimetableEntry.objects.filter(teacher=self.teacher_a, day_of_week=self.monday, period=self.period).count(), 1)
        self.assertEqual(sorted(outcomes), sorted(["created", "This teacher is already teaching another class in this slot"]))

    def test_competing_room_slot_creation_resolves_to_exactly_one_entry(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_create, class_group=self.class_a, teacher=self.teacher_a, room="Lab 1", barrier=barrier),
                pool.submit(self._attempt_create, class_group=self.class_b, teacher=self.teacher_b, room="Lab 1", barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(
            TimetableEntry.objects.filter(campus=self.campus, room_key="LAB 1", day_of_week=self.monday, period=self.period).count(), 1,
        )
        self.assertEqual(sorted(outcomes), sorted(["created", "This room is already booked in this slot"]))

    def _attempt_update(self, *, entry_id, teacher, room, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            entry = TimetableEntry.objects.get(pk=entry_id)
            update_timetable_entry(user=self.admin, tenant=self.tenant, entry=entry, teacher=teacher, room=room)
            return "done"
        finally:
            connections.close_all()

    def test_concurrent_updates_to_the_same_entry_serialize(self):
        entry = create_timetable_entry(
            user=self.admin, tenant=self.tenant, term=self.term, class_group=self.class_a,
            subject=self.subject, teacher=self.teacher_a, period=self.period, day_of_week=self.monday, room="Lab 1",
        )
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_update, entry_id=entry.id, teacher=None, room="Lab 2", barrier=barrier),
                pool.submit(self._attempt_update, entry_id=entry.id, teacher=None, room="Lab 3", barrier=barrier),
            ]
            self.assertEqual([future.result(timeout=15) for future in futures], ["done", "done"])

        entry.refresh_from_db()
        self.assertIn(entry.room, ("Lab 2", "Lab 3"))
        self.assertEqual(ActivityEvent.objects.filter(action="timetable.entry.corrected").count(), 2)
