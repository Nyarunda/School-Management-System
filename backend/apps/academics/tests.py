from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import AcademicLevel, AcademicYear, ClassGroup, StudentEnrollment, Term
from .services import enroll_student


class AcademicFoundationTests(TestCase):
    def enroll(self, **overrides):
        values = dict(user=self.user, tenant=self.school_a, student=self.student,
                      academic_year=self.year_a, academic_level=self.level_a,
                      class_group=self.class_a, campus=self.campus_a, term=self.term_a)
        values.update(overrides)
        return enroll_student(**values)

    def test_same_tenant_relationship_mismatches_are_rejected(self):
        other_year = AcademicYear.objects.create(tenant=self.school_a, name="2027",
            starts_on=date(2027, 1, 1), ends_on=date(2027, 12, 31))
        other_level = AcademicLevel.objects.create(tenant=self.school_a, name="Other", code="OTHER", sequence=9)
        other_campus = Campus.objects.create(tenant=self.school_a, name="Other", code="OTHER")
        for override in ({"academic_year": other_year}, {"academic_level": other_level}, {"campus": other_campus}):
            with self.subTest(override=override), self.assertRaises(ValidationError):
                self.enroll(**override)
        self.assertEqual(StudentEnrollment.objects.count(), 0)

    def test_stale_class_instance_is_reloaded_before_validation(self):
        other_campus = Campus.objects.create(tenant=self.school_a, name="Other", code="OTHER")
        ClassGroup.objects.filter(pk=self.class_a.pk).update(campus=other_campus)
        with self.assertRaisesMessage(ValidationError, "campus"):
            self.enroll()

    def test_duplicate_enrollment_has_one_activity_event(self):
        from apps.activity.models import ActivityEvent
        self.enroll()
        with self.assertRaisesMessage(ValidationError, "already has an enrollment"):
            self.enroll()
        self.assertEqual(StudentEnrollment.objects.count(), 1)
        self.assertEqual(ActivityEvent.objects.count(), 1)

    def test_activity_failure_rolls_back_enrollment(self):
        from unittest.mock import patch
        with patch("apps.academics.services.record_activity", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                self.enroll()
        self.assertEqual(StudentEnrollment.objects.count(), 0)

    def test_unrelated_integrity_error_is_not_reported_as_duplicate(self):
        from unittest.mock import patch
        from django.db import IntegrityError
        with patch.object(StudentEnrollment, "save", side_effect=IntegrityError("other constraint")):
            with self.assertRaises(IntegrityError):
                self.enroll()

    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.user = User.objects.create_user(username="academic-admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a,
            name="Academic administrator",
            permissions=["academics.students.enroll"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        self.campus_a = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.campus_b = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        self.year_a = AcademicYear.objects.create(
            tenant=self.school_a,
            name="2026",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            is_current=True,
        )
        self.term_a = Term.objects.create(
            tenant=self.school_a,
            academic_year=self.year_a,
            name="Term 1",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 4, 30),
            sequence=1,
        )
        self.level_a = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_a = ClassGroup.objects.create(
            tenant=self.school_a,
            name="Grade 8 East",
            code="G8-E",
            academic_level=self.level_a,
            campus=self.campus_a,
            stream="East",
        )
        self.student = Student.objects.create(
            tenant=self.school_a,
            admission_number="ADM-001",
            first_name="Amina",
            last_name="Otieno",
        )

    def test_setup_supports_configurable_levels_and_terms(self):
        self.assertEqual(self.year_a.terms.get(sequence=1), self.term_a)
        self.assertEqual(self.level_a.class_groups.get(code="G8-E"), self.class_a)

    def test_enrollment_requires_permission_and_preserves_history(self):
        first = enroll_student(
            user=self.user,
            tenant=self.school_a,
            student=self.student,
            academic_year=self.year_a,
            academic_level=self.level_a,
            class_group=self.class_a,
            campus=self.campus_a,
            term=self.term_a,
        )
        second_year = AcademicYear.objects.create(
            tenant=self.school_a,
            name="2027",
            starts_on=date(2027, 1, 1),
            ends_on=date(2027, 12, 31),
        )
        second = enroll_student(
            user=self.user,
            tenant=self.school_a,
            student=self.student,
            academic_year=second_year,
            academic_level=self.level_a,
            class_group=self.class_a,
            campus=self.campus_a,
        )

        self.assertEqual(self.student.academic_enrollments.count(), 2)
        self.assertNotEqual(first.academic_year, second.academic_year)

    def test_cross_tenant_campus_is_rejected(self):
        with self.assertRaises(ValidationError):
            enroll_student(
                user=self.user,
                tenant=self.school_a,
                student=self.student,
                academic_year=self.year_a,
                academic_level=self.level_a,
                class_group=self.class_a,
                campus=self.campus_b,
            )

        self.assertEqual(StudentEnrollment.objects.count(), 0)

    def test_user_without_academic_permission_is_rejected(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            enroll_student(
                user=self.user,
                tenant=self.school_a,
                student=self.student,
                academic_year=self.year_a,
                academic_level=self.level_a,
                class_group=self.class_a,
                campus=self.campus_a,
            )
