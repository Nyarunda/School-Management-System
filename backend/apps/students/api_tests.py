from django.test import TestCase
from rest_framework.test import APIClient

from apps.documents.testing import TemporaryDocumentStorageMixin, make_upload
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Student


class StudentApiTests(TestCase):
    def test_superuser_requires_membership_but_can_bypass_role_permission(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])
        self.assertEqual(self.client.get("/api/v1/students/", HTTP_X_TENANT_SLUG="school-a").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/students/", HTTP_X_TENANT_SLUG="school-b").status_code, 403)

    def test_disabled_tenant_and_missing_permission_are_denied(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])
        self.assertEqual(self.client.get("/api/v1/students/", HTTP_X_TENANT_SLUG="school-a").status_code, 403)
        self.role.permissions = ["students.view"]
        self.role.save(update_fields=["permissions"])
        Tenant.objects.filter(pk=self.school_a.pk).update(is_active=False)
        self.assertEqual(self.client.get("/api/v1/students/", HTTP_X_TENANT_SLUG="school-a").status_code, 403)

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


class StudentDocumentApiTests(TemporaryDocumentStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a, name="Registrar", permissions=["students.document.view", "students.document.manage"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.role)
        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        self.client.force_authenticate(self.admin)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_document_upload_download_and_delete_flow(self):
        upload_response = self.client.post(
            f"/api/v1/students/{self.student.id}/documents/",
            {"document_type": "Birth certificate", "file": make_upload(name="birth.pdf")},
            format="multipart", **self.headers(),
        )
        self.assertEqual(upload_response.status_code, 201)
        document_id = upload_response.data["id"]

        list_response = self.client.get(f"/api/v1/students/{self.student.id}/documents/", **self.headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

        download_response = self.client.get(
            f"/api/v1/students/{self.student.id}/documents/{document_id}/download/", **self.headers(),
        )
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(b"".join(download_response.streaming_content), b"%PDF-1.4 test content")

        delete_response = self.client.delete(
            f"/api/v1/students/{self.student.id}/documents/{document_id}/", **self.headers(),
        )
        self.assertEqual(delete_response.status_code, 204)
        list_after_delete = self.client.get(f"/api/v1/students/{self.student.id}/documents/", **self.headers())
        self.assertEqual(list_after_delete.data["count"], 0)

    def test_viewer_only_role_cannot_upload(self):
        viewer = User.objects.create_user(username="viewer", password="secret")
        Membership.objects.create(
            tenant=self.school_a, user=viewer,
            role=Role.objects.create(tenant=self.school_a, name="Viewer", permissions=["students.document.view"]),
        )
        self.client.force_authenticate(viewer)
        response = self.client.post(
            f"/api/v1/students/{self.student.id}/documents/",
            {"document_type": "Birth certificate", "file": make_upload()},
            format="multipart", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)
