"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, StudentEnrollment
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Application, ApplicationStatus
from .services import enroll_application


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class ApplicationEnrollConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.campus_a = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a, name="Admissions Officer",
            permissions=["admissions.enroll", "academics.students.enroll"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.role)
        self.application = Application.objects.create(
            tenant=self.school_a, application_number="APP-001", first_name="Amina", last_name="Otieno",
            status=ApplicationStatus.ACCEPTED, campus=self.campus_a,
        )
        self.year_a = AcademicYear.objects.create(
            tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31), is_current=True,
        )
        self.level_a = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_a = ClassGroup.objects.create(
            tenant=self.school_a, name="Grade 8 East", code="G8-E", academic_level=self.level_a, campus=self.campus_a,
        )

    def attempt(self, admission_number, barrier=None):
        # enroll_application's own select_for_update() on Application is the
        # natural serialization point -- rendezvous both threads right
        # before they call it (mirroring apps/leave/test_concurrency.py's
        # _attempt_final_approval), rather than intercepting a specific SQL
        # statement: the loser blocks *at* select_for_update, so it can
        # never reach a later statement to synchronize on.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            if barrier is not None:
                barrier.wait(timeout=6)
            try:
                enroll_application(
                    user=self.admin, tenant=self.school_a, application=self.application,
                    admission_number=admission_number, academic_year=self.year_a, class_group=self.class_a,
                )
                return "enrolled"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_enroll_requests_produce_exactly_one_student_and_enrollment(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.attempt, f"ADM-{index}", barrier) for index in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]

        self.assertCountEqual(outcomes, ["enrolled", "Only accepted applications can be enrolled"])
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(StudentEnrollment.objects.count(), 1)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.ENROLLED)
