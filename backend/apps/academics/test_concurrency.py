"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from apps.activity.models import ActivityEvent
from apps.students.models import Student
from .models import AcademicYear, StudentEnrollment
from .services import enroll_student
from . import tests as foundation_tests


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class EnrollmentConcurrencyTests(TransactionTestCase):
    setUp = foundation_tests.AcademicFoundationTests.setUp

    def attempt(self, year, barrier=None, student=None):
        # Each worker owns its connection and transaction, with bounded DB waits.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '4s'")

            def synchronize_insert(execute, sql, params, many, context):
                if barrier is not None and sql.startswith('INSERT INTO "academics_studentenrollment"'):
                    barrier.wait(timeout=6)
                return execute(sql, params, many, context)

            with connection.execute_wrapper(synchronize_insert):
                try:
                    enroll_student(user=self.user, tenant=self.school_a,
                        student=student or self.student, academic_year=year,
                        academic_level=self.level_a, class_group=self.class_a, campus=self.campus_a)
                    return "created"
                except ValidationError as error:
                    return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_requests_create_one_enrollment_and_event(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.attempt, self.year_a, barrier) for _ in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertCountEqual(outcomes, ["created", "Student already has an enrollment for this academic year"])
        self.assertEqual(StudentEnrollment.objects.count(), 1)
        self.assertEqual(ActivityEvent.objects.count(), 1)

    def test_different_years_reach_insert_without_serializing_on_student(self):
        other_year = AcademicYear.objects.create(tenant=self.school_a, name="2027",
            starts_on=date(2027, 1, 1), ends_on=date(2027, 12, 31))
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.attempt, year, barrier) for year in (self.year_a, other_year)]
            self.assertEqual([future.result(timeout=15) for future in futures], ["created", "created"])
        self.assertEqual(StudentEnrollment.objects.count(), 2)
        self.assertEqual(ActivityEvent.objects.count(), 2)

    def test_foreign_student_is_rejected_without_waiting_for_its_lock(self):
        foreign = Student.objects.create(tenant=self.school_b, admission_number="FOREIGN",
            first_name="Foreign", last_name="Student")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Student.objects.select_for_update().get(pk=foreign.pk)
                future = pool.submit(self.attempt, self.year_a, student=foreign)
                self.assertIn("not available in this tenant", future.result(timeout=3))
        self.assertEqual(StudentEnrollment.objects.count(), 0)
