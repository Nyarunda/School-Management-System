from django.test import TestCase
from rest_framework.test import APIClient

from apps.documents.testing import TemporaryDocumentStorageMixin, make_upload
from apps.tenancy.models import Campus, Membership, Role, Tenant, User


class StaffApiTests(TemporaryDocumentStorageMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a, name="HR Officer",
            permissions=["staff.view", "staff.manage", "staff.user_link.manage"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.role)
        self.client.force_authenticate(self.admin)

        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.other_campus = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")

        self.teacher_user = User.objects.create_user(username="teacher", password="secret")
        self.teacher_role = Role.objects.create(tenant=self.school_a, name="Teacher", permissions=[])
        Membership.objects.create(tenant=self.school_a, user=self.teacher_user, role=self.teacher_role)

        self.scoped_admin = User.objects.create_user(username="scoped-admin", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.scoped_admin, role=self.role, campus=self.campus)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_disabling_the_staff_hr_module_blocks_access_even_with_permission(self):
        from apps.platform.services import set_module_override

        set_module_override(tenant=self.school_a, module_code="staff_hr", is_enabled=False)
        response = self.client.get("/api/v1/staff/employees/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def create_employee(self, **overrides):
        payload = {
            "employee_number": "EMP-001", "first_name": "Jane", "last_name": "Doe", "job_title": "Teacher",
            "employment_type": "PERMANENT", "hire_date": "2024-01-01",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/staff/employees/", payload, format="json", **self.headers())

    def test_missing_tenant_header_is_rejected(self):
        response = self.client.get("/api/v1/staff/employees/")
        self.assertEqual(response.status_code, 404)

    def test_employee_crud(self):
        create_response = self.create_employee()
        self.assertEqual(create_response.status_code, 201)
        employee_id = create_response.data["id"]

        detail_response = self.client.get(f"/api/v1/staff/employees/{employee_id}/", **self.headers())
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["full_name"], "Jane Doe")

        update_response = self.client.patch(
            f"/api/v1/staff/employees/{employee_id}/", {"job_title": "Head Teacher"}, format="json", **self.headers(),
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data["job_title"], "Head Teacher")

    def test_duplicate_employee_number_is_a_clean_400(self):
        self.create_employee()
        response = self.create_employee(first_name="Other")
        self.assertEqual(response.status_code, 400)

    def test_employee_number_is_normalized_and_collides_case_insensitively(self):
        self.create_employee(employee_number="EMP001")
        response = self.create_employee(employee_number=" emp001 ", first_name="Other")
        self.assertEqual(response.status_code, 400)

    def test_campus_scoped_actor_cannot_create_at_a_different_campus(self):
        self.client.force_authenticate(self.scoped_admin)
        response = self.create_employee(campus=str(self.other_campus.id))
        self.assertEqual(response.status_code, 400)

    def test_campus_scoped_actor_can_create_within_own_campus(self):
        self.client.force_authenticate(self.scoped_admin)
        response = self.create_employee(campus=str(self.campus.id))
        self.assertEqual(response.status_code, 201)

    def test_campus_scoped_actor_does_not_see_another_campus_employee_in_the_list(self):
        self.create_employee(campus=str(self.other_campus.id))
        self.client.force_authenticate(self.scoped_admin)
        response = self.client.get("/api/v1/staff/employees/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_campus_scoped_actor_gets_404_for_employee_detail_outside_own_campus(self):
        create_response = self.create_employee(campus=str(self.other_campus.id))
        employee_id = create_response.data["id"]
        self.client.force_authenticate(self.scoped_admin)
        response = self.client.get(f"/api/v1/staff/employees/{employee_id}/", **self.headers())
        self.assertEqual(response.status_code, 404)

    def test_campus_scoped_actor_gets_404_for_documents_and_qualifications_outside_own_campus(self):
        create_response = self.create_employee(campus=str(self.other_campus.id))
        employee_id = create_response.data["id"]
        document_response = self.client.post(
            f"/api/v1/staff/employees/{employee_id}/documents/",
            {"document_type": "ID_COPY", "file": make_upload(name="id.pdf")},
            format="multipart", **self.headers(),
        )
        document_id = document_response.data["id"]

        self.client.force_authenticate(self.scoped_admin)
        list_response = self.client.get(f"/api/v1/staff/employees/{employee_id}/documents/", **self.headers())
        self.assertEqual(list_response.status_code, 404)
        download_response = self.client.get(
            f"/api/v1/staff/employees/{employee_id}/documents/{document_id}/download/", **self.headers(),
        )
        self.assertEqual(download_response.status_code, 404)
        qualifications_response = self.client.get(f"/api/v1/staff/employees/{employee_id}/qualifications/", **self.headers())
        self.assertEqual(qualifications_response.status_code, 404)

    def test_employee_list_is_paginated_and_filterable(self):
        self.create_employee()
        response = self.client.get(f"/api/v1/staff/employees/?campus={self.campus.id}", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

        response = self.client.get("/api/v1/staff/employees/?status=ACTIVE", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_status_transition_endpoint(self):
        create_response = self.create_employee()
        employee_id = create_response.data["id"]

        response = self.client.post(
            f"/api/v1/staff/employees/{employee_id}/status/", {"status": "SUSPENDED", "reason": "Investigation"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "SUSPENDED")

        invalid_response = self.client.post(
            f"/api/v1/staff/employees/{employee_id}/status/", {"status": "SUSPENDED"}, format="json", **self.headers(),
        )
        self.assertEqual(invalid_response.status_code, 400)

    def test_document_and_qualification_endpoints(self):
        create_response = self.create_employee()
        employee_id = create_response.data["id"]

        document_response = self.client.post(
            f"/api/v1/staff/employees/{employee_id}/documents/",
            {"document_type": "ID_COPY", "file": make_upload(name="id.pdf")},
            format="multipart", **self.headers(),
        )
        self.assertEqual(document_response.status_code, 201)
        self.assertEqual(document_response.data["original_filename"], "id.pdf")
        document_id = document_response.data["id"]

        list_response = self.client.get(f"/api/v1/staff/employees/{employee_id}/documents/", **self.headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

        download_response = self.client.get(
            f"/api/v1/staff/employees/{employee_id}/documents/{document_id}/download/", **self.headers(),
        )
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(b"".join(download_response.streaming_content), b"%PDF-1.4 test content")

        delete_response = self.client.delete(
            f"/api/v1/staff/employees/{employee_id}/documents/{document_id}/", **self.headers(),
        )
        self.assertEqual(delete_response.status_code, 204)
        list_after_delete = self.client.get(f"/api/v1/staff/employees/{employee_id}/documents/", **self.headers())
        self.assertEqual(list_after_delete.data["count"], 0)

        qualification_response = self.client.post(
            f"/api/v1/staff/employees/{employee_id}/qualifications/",
            {"title": "B.Ed Mathematics", "institution": "University", "year_obtained": 2015},
            format="json", **self.headers(),
        )
        self.assertEqual(qualification_response.status_code, 201)

    def test_document_upload_rejects_a_json_body(self):
        # The old stub accepted a JSON {"file_name": "..."} body and created
        # a fake row with no real file behind it. The multipart-only upload
        # endpoint must reject that shape cleanly rather than silently
        # reviving the old stub behavior.
        create_response = self.create_employee()
        employee_id = create_response.data["id"]
        response = self.client.post(
            f"/api/v1/staff/employees/{employee_id}/documents/", {"document_type": "ID_COPY", "file_name": "id.pdf"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 415)

    def test_user_link_and_unlink_flow(self):
        create_response = self.create_employee()
        employee_id = create_response.data["id"]

        link_response = self.client.post(
            f"/api/v1/staff/employees/{employee_id}/user-link/", {"user_id": str(self.teacher_user.id)},
            format="json", **self.headers(),
        )
        self.assertEqual(link_response.status_code, 200)
        self.assertEqual(link_response.data["user_account"], self.teacher_user.id)

        unlink_response = self.client.delete(f"/api/v1/staff/employees/{employee_id}/user-link/", **self.headers())
        self.assertEqual(unlink_response.status_code, 200)
        self.assertIsNone(unlink_response.data["user_account"])

    def test_user_link_requires_its_own_permission(self):
        limited_role = Role.objects.create(tenant=self.school_a, name="Limited HR", permissions=["staff.view", "staff.manage"])
        limited_user = User.objects.create_user(username="limited", password="secret")
        Membership.objects.create(tenant=self.school_a, user=limited_user, role=limited_role)
        self.client.force_authenticate(limited_user)

        create_response = self.create_employee()
        employee_id = create_response.data["id"]
        response = self.client.post(
            f"/api/v1/staff/employees/{employee_id}/user-link/", {"user_id": str(self.teacher_user.id)},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)
