from django.test import TestCase
from rest_framework.test import APIClient

from apps.activity.models import ActivityEvent
from apps.documents.testing import TemporaryDocumentStorageMixin
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import ReportExportJob
from .services import generate_report_export


class ReportingApiTests(TemporaryDocumentStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.tenant, name="Admin", permissions=["reports.students.view", "reports.students.export"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=self.admin_role)

        self.viewer = User.objects.create_user(username="viewer", password="secret")
        self.viewer_role = Role.objects.create(tenant=self.tenant, name="Viewer", permissions=["reports.students.view"])
        Membership.objects.create(tenant=self.tenant, user=self.viewer, role=self.viewer_role)

        Student.objects.create(tenant=self.tenant, admission_number="ADM-001", first_name="Amina", last_name="Otieno")

        self.client.force_authenticate(self.admin)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_missing_tenant_header_is_rejected(self):
        response = self.client.get("/api/v1/reports/catalogue/")
        self.assertEqual(response.status_code, 404)

    def test_catalogue_is_filtered_by_permission(self):
        response = self.client.get("/api/v1/reports/catalogue/", **self.headers())
        self.assertEqual(response.status_code, 200)
        codes = {entry["code"] for entry in response.data}
        self.assertIn("students.enrollment_register", codes)
        self.assertNotIn("finance.fee_statement", codes)

    def test_viewer_role_sees_view_only_in_catalogue(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/v1/reports/catalogue/", **self.headers())
        entry = next(e for e in response.data if e["code"] == "students.enrollment_register")
        self.assertTrue(entry["can_view"])
        self.assertFalse(entry["can_export"])

    def test_preview_returns_rows(self):
        response = self.client.get("/api/v1/reports/students.enrollment_register/preview/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["rows"]), 1)
        self.assertFalse(response.data["has_more"])

    def test_preview_query_shape_is_bounded(self):
        for index in range(2, 5):
            Student.objects.create(tenant=self.tenant, admission_number=f"ADM-00{index}", first_name="Student", last_name=str(index))

        # Milestone 22.3 permanent query-count regression coverage.
        with self.assertNumQueries(6):
            response = self.client.get("/api/v1/reports/students.enrollment_register/preview/", **self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["rows"]), 4)

    def test_disabling_the_underlying_module_blocks_preview_even_with_permission(self):
        from apps.platform.services import set_module_override

        set_module_override(tenant=self.tenant, module_code="student_records", is_enabled=False)
        response = self.client.get("/api/v1/reports/students.enrollment_register/preview/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_preview_rejects_an_unknown_report_code(self):
        response = self.client.get("/api/v1/reports/not.a.real.report/preview/", **self.headers())
        self.assertEqual(response.status_code, 400)

    def test_viewer_cannot_export(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post("/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_export_request_returns_202_with_a_pending_job(self):
        response = self.client.post("/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers())
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "PENDING")
        self.assertFalse(response.data["download_available"])

    def test_export_job_detail_and_download_flow(self):
        create_response = self.client.post(
            "/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers(),
        )
        job = ReportExportJob.objects.get(pk=create_response.data["id"])
        generate_report_export(job=job)

        detail_response = self.client.get(f"/api/v1/reports/exports/{job.id}/", **self.headers())
        self.assertEqual(detail_response.status_code, 200)
        self.assertTrue(detail_response.data["download_available"])
        self.assertEqual(detail_response.data["row_count"], 1)

        download_response = self.client.get(f"/api/v1/reports/exports/{job.id}/download/", **self.headers())
        self.assertEqual(download_response.status_code, 200)
        content = b"".join(download_response.streaming_content).decode("utf-8")
        self.assertIn("ADM-001", content)

    def test_successful_download_records_one_activity_event(self):
        create_response = self.client.post(
            "/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers(),
        )
        job = ReportExportJob.objects.get(pk=create_response.data["id"])
        generate_report_export(job=job)

        self.client.get(f"/api/v1/reports/exports/{job.id}/download/", **self.headers())
        event = ActivityEvent.objects.get(action="report.export.downloaded")
        self.assertEqual(event.metadata["job_id"], str(job.id))
        self.assertEqual(event.metadata["document_id"], str(job.document_id))

    def test_denied_download_records_no_activity_event(self):
        create_response = self.client.post(
            "/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers(),
        )
        job = ReportExportJob.objects.get(pk=create_response.data["id"])
        generate_report_export(job=job)

        self.client.force_authenticate(self.viewer)
        self.client.get(f"/api/v1/reports/exports/{job.id}/download/", **self.headers())
        self.assertFalse(ActivityEvent.objects.filter(action="report.export.downloaded").exists())

    def test_viewer_cannot_download(self):
        create_response = self.client.post(
            "/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers(),
        )
        job = ReportExportJob.objects.get(pk=create_response.data["id"])
        generate_report_export(job=job)

        self.client.force_authenticate(self.viewer)
        response = self.client.get(f"/api/v1/reports/exports/{job.id}/download/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_download_not_ready_yet_is_a_clean_404(self):
        create_response = self.client.post(
            "/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers(),
        )
        job_id = create_response.data["id"]
        response = self.client.get(f"/api/v1/reports/exports/{job_id}/download/", **self.headers())
        self.assertEqual(response.status_code, 404)
        self.assertIn("not ready yet", str(response.data["detail"]))

    def test_download_of_an_expired_export_says_expired_not_not_ready(self):
        create_response = self.client.post(
            "/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers(),
        )
        job = ReportExportJob.objects.get(pk=create_response.data["id"])
        generate_report_export(job=job)
        job.mark_processed()
        # Simulate the retention purge: the document is gone but the job
        # stays PROCESSED with its row_count intact.
        job.document = None
        job.save(update_fields=["document"])

        response = self.client.get(f"/api/v1/reports/exports/{job.id}/download/", **self.headers())
        self.assertEqual(response.status_code, 404)
        self.assertIn("expired", str(response.data["detail"]))

    def test_download_of_a_failed_export_surfaces_the_failure_reason(self):
        create_response = self.client.post(
            "/api/v1/reports/students.enrollment_register/export/", {}, format="json", **self.headers(),
        )
        job = ReportExportJob.objects.get(pk=create_response.data["id"])
        job.status = "FAILED"
        job.last_error = "boom"
        job.save(update_fields=["status", "last_error"])

        response = self.client.get(f"/api/v1/reports/exports/{job.id}/download/", **self.headers())
        self.assertEqual(response.status_code, 404)
        self.assertIn("boom", str(response.data["detail"]))
