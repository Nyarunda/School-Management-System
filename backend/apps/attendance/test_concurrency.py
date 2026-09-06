"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, EnrollmentStatus, StudentEnrollment, Subject, TeacherAssignment
from apps.activity.models import ActivityEvent
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import AttendanceRecord, AttendanceSession, AttendanceStatus
from .services import open_attendance_session, record_attendance_bulk


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class AttendanceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="teacher", password="secret")
        self.role = Role.objects.create(
            tenant=self.tenant, name="Teacher", permissions=["attendance.session.manage", "attendance.record.view"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.user, role=self.role)
        self.campus = Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")
        self.year = AcademicYear.objects.create(tenant=self.tenant, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        self.level = AcademicLevel.objects.create(tenant=self.tenant, name="Grade 8", code="G8", sequence=8)
        self.class_group = ClassGroup.objects.create(tenant=self.tenant, name="Grade 8 East", code="G8-E", academic_level=self.level, campus=self.campus)
        self.subject = Subject.objects.create(tenant=self.tenant, name="Math", code="MATH")
        TeacherAssignment.objects.create(tenant=self.tenant, teacher=self.user, class_group=self.class_group, subject=self.subject)
        self.student = Student.objects.create(tenant=self.tenant, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, academic_year=self.year, academic_level=self.level,
            class_group=self.class_group, campus=self.campus, status=EnrollmentStatus.ACTIVE,
        )
        self.session_date = date(2026, 3, 2)  # a Monday

    def attempt_open(self, barrier=None):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")

            def synchronize_insert(execute, sql, params, many, context):
                if barrier is not None and sql.startswith('INSERT INTO "attendance_attendancesession"'):
                    barrier.wait(timeout=6)
                return execute(sql, params, many, context)

            with connection.execute_wrapper(synchronize_insert):
                try:
                    open_attendance_session(user=self.user, tenant=self.tenant, class_group=self.class_group, session_date=self.session_date)
                    return "created"
                except ValidationError as error:
                    return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_session_opens_create_exactly_one_session(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.attempt_open, barrier) for _ in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]
        # Both racing calls resolve to the same session (get-or-create,
        # never a rejection) -- unlike enroll_student's uniqueness
        # violation, opening the same session twice is not an error.
        self.assertEqual(outcomes, ["created", "created"])
        self.assertEqual(AttendanceSession.objects.count(), 1)

    def attempt_record(self, status, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            session = AttendanceSession.objects.get(tenant=self.tenant, class_group=self.class_group, session_date=self.session_date)
            record_attendance_bulk(user=self.user, tenant=self.tenant, session=session, entries=[{"student": self.student, "status": status}])
            return "done"
        finally:
            connections.close_all()

    def test_concurrent_corrections_serialize_without_a_lost_update(self):
        session, _ = open_attendance_session(user=self.user, tenant=self.tenant, class_group=self.class_group, session_date=self.session_date)
        record_attendance_bulk(user=self.user, tenant=self.tenant, session=session, entries=[{"student": self.student, "status": AttendanceStatus.PRESENT}])

        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.attempt_record, AttendanceStatus.ABSENT, barrier),
                pool.submit(self.attempt_record, AttendanceStatus.LATE, barrier),
            ]
            self.assertEqual([future.result(timeout=15) for future in futures], ["done", "done"])

        # The session lock forces the two concurrent corrections to
        # serialize -- the final status is whichever transaction committed
        # last, never a corrupted mix, and each transition that actually
        # changed the status logged its own activity event (no lost update).
        record = AttendanceRecord.objects.get(session=session, student=self.student)
        self.assertIn(record.status, (AttendanceStatus.ABSENT, AttendanceStatus.LATE))
        self.assertEqual(ActivityEvent.objects.filter(action="attendance.record.corrected").count(), 2)
