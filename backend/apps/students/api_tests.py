from django.test import TestCase
from rest_framework.test import APIClient

from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Student


class StudentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.campus_a = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.campus_b = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        self.user = User.objects.create_user(username="viewer", password="secret")
        self.role = Role.objects.create(tenant=self.school_a, name="Viewer", permissions=["students.view"])
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        Student.objects.create(
            tenant=self.school_a,
            admission_number="ADM-001",
            first_name="Amina",
            last_name="Otieno",
            campus=self.campus_a,
        )
        Student.objects.create(
            tenant=self.school_b,
            admission_number="ADM-001",
            first_name="Peter",
            last_name="Kamau",
            campus=self.campus_b,
        )
        self.client.force_authenticate(self.user)

    def test_student_list_requires_tenant_context(self):
        response = self.client.get("/api/v1/students/")

        self.assertEqual(response.status_code, 404)

    def test_student_list_is_permission_and_tenant_scoped(self):
        response = self.client.get("/api/v1/students/", HTTP_X_TENANT_SLUG="school-a")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["admission_number"], "ADM-001")

    def test_student_list_query_shape_is_bounded(self):
        with self.assertNumQueries(3):
            response = self.client.get("/api/v1/students/", HTTP_X_TENANT_SLUG="school-a")

        self.assertEqual(response.status_code, 200)