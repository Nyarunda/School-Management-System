from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.students.models import Student
from apps.tenancy.models import Tenant

from .models import Guardian
from .services import link_guardian


class GuardianIsolationTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.student = Student.objects.create(
            tenant=self.school_a,
            admission_number="ADM-001",
            first_name="Amina",
            last_name="Otieno",
        )
        self.guardian_b = Guardian.objects.create(
            tenant=self.school_b,
            first_name="Peter",
            last_name="Otieno",
            phone_number="0700000000",
        )

    def test_guardian_from_another_tenant_cannot_be_linked(self):
        with self.assertRaises(ValidationError):
            link_guardian(student=self.student, guardian=self.guardian_b, relationship="Parent")