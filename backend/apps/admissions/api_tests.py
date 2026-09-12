from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Application, ApplicationStatus


class ApplicationEnrollApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.campus_a = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a, name="Admissions Officer",
            permissions=["admissions.enroll", "academics.students.enroll"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.role)

        self.application = Application.objects.create(
            tenant=self.school_a, application_number="APP-001", first_name="Amina", last_name="Otieno",
            date_of_birth=date(2014, 5, 10), status=ApplicationStatus.ACCEPTED, campus=self.campus_a,
        )
        self.year_a = AcademicYear.objects.create(
            tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31), is_current=True,
        )
        self.level_a = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_a = ClassGroup.objects.create(
            tenant=self.school_a, name="Grade 8 East", code="G8-E", academic_level=self.level_a, campus=self.campus_a,
        )
        self.client.force_authenticate(self.admin)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def payload(self, **overrides):
        data = {
            "admission_number": "ADM-001",
            "academic_year": str(self.year_a.id),
            "class_group": str(self.class_a.id),
        }
        data.update(overrides)
        return data

    def test_enroll_endpoint_creates_student_and_placement(self):
        response = self.client.post(
            f"/api/v1/admissions/applications/{self.application.id}/enroll/",
            self.payload(), format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["application_status"], "ENROLLED")
        self.assertEqual(Student.objects.filter(admission_number="ADM-001").count(), 1)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.ENROLLED)

    def test_enroll_endpoint_requires_tenant_context(self):
        response = self.client.post(
            f"/api/v1/admissions/applications/{self.application.id}/enroll/", self.payload(), format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_enroll_endpoint_requires_permission(self):
        self.role.permissions = ["academics.students.enroll"]
        self.role.save(update_fields=["permissions"])
        response = self.client.post(
            f"/api/v1/admissions/applications/{self.application.id}/enroll/",
            self.payload(), format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_enroll_endpoint_404s_for_a_cross_tenant_application(self):
        foreign_application = Application.objects.create(
            tenant=self.school_b, application_number="APP-100", first_name="Peter", last_name="Kamau",
            status=ApplicationStatus.ACCEPTED,
        )
        response = self.client.post(
            f"/api/v1/admissions/applications/{foreign_application.id}/enroll/",
            self.payload(), format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 404)

    def test_enroll_endpoint_rejects_a_non_accepted_application(self):
        self.application.status = ApplicationStatus.SUBMITTED
        self.application.save(update_fields=["status"])
        response = self.client.post(
            f"/api/v1/admissions/applications/{self.application.id}/enroll/",
            self.payload(), format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Student.objects.count(), 0)
