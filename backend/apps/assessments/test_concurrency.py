"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, EnrollmentStatus, StudentEnrollment, Subject, TeacherAssignment, Term
from apps.activity.models import ActivityEvent
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Assessment, AssessmentGradingBand, AssessmentResult, AssessmentType, GradingBand, GradingScheme, MarkStatus
from .services import add_grading_band, create_assessment, record_assessment_marks


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class AssessmentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="teacher", password="secret")
        self.role = Role.objects.create(
            tenant=self.tenant, name="Teacher",
            permissions=["assessment.manage", "assessment.marks.manage", "assessment.record.view", "assessment.setup.manage"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.user, role=self.role)
        self.campus = Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")
        self.year = AcademicYear.objects.create(tenant=self.tenant, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        self.term = Term.objects.create(tenant=self.tenant, academic_year=self.year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        self.level = AcademicLevel.objects.create(tenant=self.tenant, name="Grade 8", code="G8", sequence=8)
        self.class_group = ClassGroup.objects.create(tenant=self.tenant, name="Grade 8 East", code="G8-E", academic_level=self.level, campus=self.campus)
        self.subject = Subject.objects.create(tenant=self.tenant, name="Math", code="MATH")
        TeacherAssignment.objects.create(tenant=self.tenant, teacher=self.user, class_group=self.class_group, subject=self.subject)
        self.assessment_type = AssessmentType.objects.create(tenant=self.tenant, name="CAT", code="CAT")
        self.student = Student.objects.create(tenant=self.tenant, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        StudentEnrollment.objects.create(
            tenant=self.tenant, student=self.student, academic_year=self.year, academic_level=self.level,
            class_group=self.class_group, campus=self.campus, status=EnrollmentStatus.ACTIVE,
        )
        scheme = GradingScheme.objects.create(tenant=self.tenant, name="Standard", academic_level=self.level)
        add_grading_band(user=self.user, tenant=self.tenant, scheme=scheme, grade_label="A", min_percentage=Decimal("80"), max_percentage=Decimal("100"))
        add_grading_band(user=self.user, tenant=self.tenant, scheme=scheme, grade_label="B", min_percentage=Decimal("0"), max_percentage=Decimal("79.99"))

    def attempt_create(self, barrier=None):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")

            def synchronize_insert(execute, sql, params, many, context):
                if barrier is not None and sql.startswith('INSERT INTO "assessments_assessment"'):
                    barrier.wait(timeout=6)
                return execute(sql, params, many, context)

            with connection.execute_wrapper(synchronize_insert):
                try:
                    create_assessment(
                        user=self.user, tenant=self.tenant, term=self.term, class_group=self.class_group,
                        subject=self.subject, assessment_type=self.assessment_type, name="CAT 1",
                        max_marks=Decimal("100"), scheduled_date=date(2026, 2, 1),
                    )
                    return "created"
                except ValidationError as error:
                    return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_assessment_creation_produces_exactly_one_consistent_snapshot(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.attempt_create, barrier) for _ in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(outcomes, ["created", "created"])
        self.assertEqual(Assessment.objects.count(), 1)
        assessment = Assessment.objects.get()
        self.assertEqual(AssessmentResult.objects.filter(assessment=assessment).count(), 1)  # exactly one roster student
        self.assertEqual(AssessmentGradingBand.objects.filter(assessment=assessment).count(), 2)  # exactly the two snapshot bands

    def attempt_record(self, score, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            assessment = Assessment.objects.get(tenant=self.tenant, term=self.term, class_group=self.class_group, subject=self.subject, name="CAT 1")
            record_assessment_marks(
                user=self.user, tenant=self.tenant, assessment=assessment,
                entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": score}],
            )
            return "done"
        finally:
            connections.close_all()

    def test_concurrent_corrections_serialize_without_a_lost_update(self):
        assessment, _ = create_assessment(
            user=self.user, tenant=self.tenant, term=self.term, class_group=self.class_group,
            subject=self.subject, assessment_type=self.assessment_type, name="CAT 1",
            max_marks=Decimal("100"), scheduled_date=date(2026, 2, 1),
        )
        record_assessment_marks(
            user=self.user, tenant=self.tenant, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("50")}],
        )

        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.attempt_record, Decimal("60"), barrier),
                pool.submit(self.attempt_record, Decimal("70"), barrier),
            ]
            self.assertEqual([future.result(timeout=15) for future in futures], ["done", "done"])

        result = AssessmentResult.objects.get(assessment=assessment, student=self.student)
        self.assertIn(result.score, (Decimal("60.00"), Decimal("70.00")))
        self.assertEqual(ActivityEvent.objects.filter(action="assessment.result.corrected").count(), 2)
