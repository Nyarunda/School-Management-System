from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.students.models import Student
from apps.tenancy.models import Campus, Tenant, User

from .models import Application, ApplicationStatus
from .services import enroll_application, transition_application


class AdmissionLifecycleTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.campus_a = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.campus_b = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        self.reviewer = User.objects.create_user(username="reviewer", password="secret")
        self.application = Application.objects.create(
            tenant=self.school_a,
            application_number="APP-001",
            first_name="Amina",
            last_name="Otieno",
            date_of_birth=date(2014, 5, 10),
            campus=self.campus_a,
        )

    def test_application_requires_ordered_status_transitions(self):
        transition_application(application=self.application, status=ApplicationStatus.SUBMITTED)
        transition_application(application=self.application, status=ApplicationStatus.UNDER_REVIEW)
        transition_application(application=self.application, status=ApplicationStatus.ACCEPTED, reviewer=self.reviewer)

        self.assertEqual(self.application.status, ApplicationStatus.ACCEPTED)
        with self.assertRaises(ValidationError):
            transition_application(application=self.application, status=ApplicationStatus.REJECTED)

    def test_enrollment_creates_tenant_owned_student_and_closes_application(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])

        student = enroll_application(
            application=self.application,
            admission_number="ADM-001",
            campus=self.campus_a,
            actor=self.reviewer,
        )

        self.assertEqual(student.tenant, self.school_a)
        self.assertEqual(student.admission_number, "ADM-001")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.ENROLLED)

    def test_cross_tenant_campus_cannot_be_used_for_enrollment(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            enroll_application(application=self.application, admission_number="ADM-002", campus=self.campus_b)

        self.assertEqual(Student.objects.count(), 0)