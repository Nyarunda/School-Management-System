import dataclasses
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.documents.testing import TemporaryDocumentStorageMixin
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .catalogue import REPORT_CATALOGUE, get_report_definition, validate_and_coerce_params
from .models import ReportExportJob
from .services import generate_report_export, request_report_export, run_report_preview


class CatalogueValidationTests(TestCase):
    def test_missing_required_parameter_is_rejected(self):
        definition = get_report_definition("finance.fee_statement")
        with self.assertRaises(ValidationError):
            validate_and_coerce_params(definition=definition, raw_params={})

    def test_unknown_parameter_is_rejected(self):
        definition = get_report_definition("students.enrollment_register")
        with self.assertRaises(ValidationError):
            validate_and_coerce_params(definition=definition, raw_params={"bogus": "x"})

    def test_default_is_applied_when_omitted(self):
        definition = get_report_definition("finance.fee_statement")
        coerced = validate_and_coerce_params(
            definition=definition, raw_params={"student_id": "00000000-0000-0000-0000-000000000000"},
        )
        self.assertIsNotNone(coerced["as_of"])

    def test_date_range_exceeding_the_cap_is_rejected(self):
        definition = get_report_definition("finance.collections_summary")
        with self.assertRaises(ValidationError):
            validate_and_coerce_params(
                definition=definition, raw_params={"start_date": "2020-01-01", "end_date": "2026-01-01"},
            )

    def test_end_before_start_is_rejected(self):
        definition = get_report_definition("attendance.absence_summary")
        with self.assertRaises(ValidationError):
            validate_and_coerce_params(
                definition=definition, raw_params={"start_date": "2026-02-01", "end_date": "2026-01-01"},
            )

    def test_unknown_report_code_is_rejected(self):
        with self.assertRaises(ValidationError):
            get_report_definition("not.a.real.report")

    def test_invalid_uuid_is_rejected(self):
        definition = get_report_definition("finance.fee_statement")
        with self.assertRaises(ValidationError):
            validate_and_coerce_params(definition=definition, raw_params={"student_id": "not-a-uuid"})


class ReportingFoundationTests(TemporaryDocumentStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.tenant, name="Admin", permissions=["reports.students.view", "reports.students.export"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=self.role)
        for index in range(3):
            Student.objects.create(tenant=self.tenant, admission_number=f"ADM-00{index}", first_name=f"Student{index}", last_name="Test")


class RunReportPreviewTests(ReportingFoundationTests):
    def test_preview_returns_bounded_rows(self):
        result = run_report_preview(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        self.assertEqual(result["total_count"], 3)
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["columns"], REPORT_CATALOGUE["students.enrollment_register"].columns)

    def test_preview_paginates(self):
        result = run_report_preview(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={}, page=1, page_size=2,
        )
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["total_count"], 3)

        second_page = run_report_preview(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={}, page=2, page_size=2,
        )
        self.assertEqual(len(second_page["rows"]), 1)

    def test_page_size_is_capped(self):
        result = run_report_preview(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={}, page=1, page_size=10_000,
        )
        self.assertEqual(result["total_count"], 3)
        self.assertEqual(len(result["rows"]), 3)

    def test_requires_view_permission(self):
        viewer = User.objects.create_user(username="viewer", password="secret")
        Membership.objects.create(
            tenant=self.tenant, user=viewer, role=Role.objects.create(tenant=self.tenant, name="No Access", permissions=[]),
        )
        with self.assertRaises(ValidationError):
            run_report_preview(user=viewer, tenant=self.tenant, report_code="students.enrollment_register", params={})

    def test_unknown_report_code_is_rejected(self):
        with self.assertRaises(ValidationError):
            run_report_preview(user=self.admin, tenant=self.tenant, report_code="not.a.real.report", params={})


class RequestReportExportTests(ReportingFoundationTests):
    def test_export_request_only_creates_a_job_never_a_document(self):
        job = request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        self.assertIsNone(job.document)
        self.assertIsNone(job.row_count)
        self.assertEqual(job.status, "PENDING")
        self.assertEqual(ReportExportJob.objects.filter(tenant=self.tenant).count(), 1)

    def test_requires_export_permission(self):
        viewer = User.objects.create_user(username="viewer", password="secret")
        Membership.objects.create(
            tenant=self.tenant, user=viewer,
            role=Role.objects.create(tenant=self.tenant, name="ViewOnly", permissions=["reports.students.view"]),
        )
        with self.assertRaises(ValidationError):
            request_report_export(user=viewer, tenant=self.tenant, report_code="students.enrollment_register", params={})

    def test_invalid_params_are_rejected_before_a_job_is_created(self):
        with self.assertRaises(ValidationError):
            request_report_export(user=self.admin, tenant=self.tenant, report_code="finance.fee_statement", params={})
        self.assertEqual(ReportExportJob.objects.filter(tenant=self.tenant).count(), 0)


class GenerateReportExportTests(ReportingFoundationTests):
    def test_generate_creates_a_csv_document_with_the_right_row_count(self):
        job = request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        generate_report_export(job=job)
        job.refresh_from_db()
        self.assertEqual(job.row_count, 3)
        self.assertIsNotNone(job.document)
        self.assertEqual(job.document.content_type, "text/csv")
        self.assertIsNotNone(job.document.retention_expires_at)

    def test_generated_csv_contains_the_expected_rows(self):
        from apps.documents.services import open_document_stream

        job = request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        generate_report_export(job=job)
        job.refresh_from_db()
        with open_document_stream(document=job.document) as stream:
            content = stream.read().decode("utf-8")
        self.assertIn("Admission No.", content)  # header row
        self.assertIn("ADM-000", content)
        self.assertEqual(content.strip().count("\n") + 1, 4)  # header + 3 students

    def test_exceeding_max_rows_fails_the_job_without_creating_a_document(self):
        job = request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        tiny_definition = dataclasses.replace(REPORT_CATALOGUE["students.enrollment_register"], max_rows=1)
        with patch("apps.reporting.services.get_report_definition", return_value=tiny_definition):
            with self.assertRaises(ValidationError):
                generate_report_export(job=job)
        job.refresh_from_db()
        self.assertIsNone(job.document)
