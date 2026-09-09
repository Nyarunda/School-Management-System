import dataclasses
from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.academics.models import AcademicLevel, ClassGroup
from apps.activity.models import ActivityEvent
from apps.documents.testing import TemporaryDocumentStorageMixin
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

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
        self.assertEqual(len(result["rows"]), 3)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["columns"], REPORT_CATALOGUE["students.enrollment_register"].columns)

    def test_preview_paginates(self):
        result = run_report_preview(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={}, page=1, page_size=2,
        )
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(result["has_more"])

        second_page = run_report_preview(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={}, page=2, page_size=2,
        )
        self.assertEqual(len(second_page["rows"]), 1)
        self.assertFalse(second_page["has_more"])

    def test_page_size_is_capped(self):
        result = run_report_preview(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={}, page=1, page_size=10_000,
        )
        self.assertEqual(len(result["rows"]), 3)

    def test_preview_beyond_max_preview_rows_is_rejected(self):
        with self.assertRaises(ValidationError):
            run_report_preview(
                user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={},
                page=41, page_size=25,
            )

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

    def test_requester_with_no_active_membership_fails_closed(self):
        """A job that sits PENDING long enough for its requester to lose
        access (role changed, membership deactivated) must not still
        generate the file -- re-authorization happens at generation time,
        not just at request time.
        """
        job = request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        membership = Membership.objects.get(tenant=self.tenant, user=self.admin)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        with self.assertRaises(ValidationError):
            generate_report_export(job=job)
        job.refresh_from_db()
        self.assertIsNone(job.document)

    def test_requester_removed_entirely_fails_closed(self):
        job = request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        job.requested_by = None
        job.save(update_fields=["requested_by"])
        with self.assertRaises(ValidationError):
            generate_report_export(job=job)

    def test_csv_cells_starting_with_formula_characters_are_neutralized(self):
        from apps.documents.services import open_document_stream

        Student.objects.create(tenant=self.tenant, admission_number="=2+2", first_name="Evil", last_name="Formula")
        job = request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        generate_report_export(job=job)
        job.refresh_from_db()
        with open_document_stream(document=job.document) as stream:
            content = stream.read().decode("utf-8")
        self.assertIn("'=2+2", content)
        self.assertNotIn("\n=2+2", content)


class ReportExportAuditTests(ReportingFoundationTests):
    """RC Area 3: export request/download previously had no audit trail at
    all, unlike Finance/Leave/Assessments/Attendance. Generation (a worker
    event) is deliberately not what's asserted here -- request and download
    are the security-relevant user actions.
    """

    def test_request_report_export_records_one_activity_event(self):
        job = request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        event = ActivityEvent.objects.get(action="report.export.requested")
        self.assertEqual(event.metadata["job_id"], str(job.id))
        self.assertEqual(event.metadata["report_code"], "students.enrollment_register")
        self.assertNotIn("params", event.metadata)

    def test_idempotent_replay_still_records_a_request_event_each_time(self):
        request_report_export(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register",
            params={}, idempotency_key="click-1",
        )
        request_report_export(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register",
            params={}, idempotency_key="click-1",
        )
        self.assertEqual(ActivityEvent.objects.filter(action="report.export.requested").count(), 2)


class ReportExportIdempotencyTests(ReportingFoundationTests):
    def test_repeat_request_with_the_same_key_returns_the_same_job(self):
        first = request_report_export(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={},
            idempotency_key="click-1",
        )
        second = request_report_export(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={},
            idempotency_key="click-1",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(ReportExportJob.objects.filter(tenant=self.tenant).count(), 1)

    def test_repeat_key_with_different_params_is_rejected(self):
        request_report_export(
            user=self.admin, tenant=self.tenant, report_code="students.enrollment_register",
            params={"status": "ACTIVE"}, idempotency_key="click-1",
        )
        with self.assertRaises(ValidationError):
            request_report_export(
                user=self.admin, tenant=self.tenant, report_code="students.enrollment_register",
                params={"status": "GRADUATED"}, idempotency_key="click-1",
            )

    def test_omitting_the_key_always_creates_a_new_job(self):
        request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        request_report_export(user=self.admin, tenant=self.tenant, report_code="students.enrollment_register", params={})
        self.assertEqual(ReportExportJob.objects.filter(tenant=self.tenant).count(), 2)


class CampusScopeAuthorizationTests(TestCase):
    """Milestone 20.1 -- the same Membership.campus scoping already enforced
    in timetable/assessments/attendance/leave/staff services.py must also
    apply to Reporting's campus-filterable reports.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.campus_a = Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")
        self.campus_b = Campus.objects.create(tenant=self.tenant, name="Annex", code="ANNEX")
        self.role = Role.objects.create(
            tenant=self.tenant, name="Campus Admin",
            permissions=["reports.students.view", "reports.attendance.view", "reports.staff.view", "reports.assessments.view"],
        )
        self.campus_user = User.objects.create_user(username="campus-admin", password="secret")
        Membership.objects.create(tenant=self.tenant, user=self.campus_user, role=self.role, campus=self.campus_a)
        Student.objects.create(tenant=self.tenant, admission_number="ADM-100", first_name="A", last_name="B", campus=self.campus_b)

        self.level = AcademicLevel.objects.create(tenant=self.tenant, name="Grade 8", code="G8", sequence=8)
        self.class_campus_a = ClassGroup.objects.create(tenant=self.tenant, name="Main G8", code="MAIN-G8", academic_level=self.level, campus=self.campus_a)
        self.class_campus_b = ClassGroup.objects.create(tenant=self.tenant, name="Annex G8", code="ANNEX-G8", academic_level=self.level, campus=self.campus_b)

    def test_enrollment_register_rejects_a_different_campus(self):
        with self.assertRaises(ValidationError):
            run_report_preview(
                user=self.campus_user, tenant=self.tenant, report_code="students.enrollment_register",
                params={"campus_id": str(self.campus_b.id)},
            )

    def test_enrollment_register_allows_the_user_s_own_campus(self):
        result = run_report_preview(
            user=self.campus_user, tenant=self.tenant, report_code="students.enrollment_register",
            params={"campus_id": str(self.campus_a.id)},
        )
        self.assertEqual(result["rows"], [])

    def test_absence_summary_rejects_a_different_campus(self):
        with self.assertRaises(ValidationError):
            run_report_preview(
                user=self.campus_user, tenant=self.tenant, report_code="attendance.absence_summary",
                params={"start_date": "2026-01-01", "end_date": "2026-01-02", "campus_id": str(self.campus_b.id)},
            )

    def test_employee_register_rejects_a_different_campus(self):
        with self.assertRaises(ValidationError):
            run_report_preview(
                user=self.campus_user, tenant=self.tenant, report_code="staff.employee_register",
                params={"campus_id": str(self.campus_b.id)},
            )

    def test_results_sheet_rejects_a_different_campus(self):
        with self.assertRaisesMessage(ValidationError, "not authorized for this campus"):
            run_report_preview(
                user=self.campus_user, tenant=self.tenant, report_code="assessments.results_sheet",
                params={"class_group_id": str(self.class_campus_b.id), "term_id": "00000000-0000-0000-0000-000000000000"},
            )

    def test_results_sheet_allows_the_user_s_own_campus(self):
        result = run_report_preview(
            user=self.campus_user, tenant=self.tenant, report_code="assessments.results_sheet",
            params={"class_group_id": str(self.class_campus_a.id), "term_id": "00000000-0000-0000-0000-000000000000"},
        )
        self.assertEqual(result["rows"], [])

    def test_a_tenant_wide_user_may_query_any_campus(self):
        hq_user = User.objects.create_user(username="hq-admin", password="secret")
        Membership.objects.create(tenant=self.tenant, user=hq_user, role=self.role)  # campus=None: tenant-wide
        result = run_report_preview(
            user=hq_user, tenant=self.tenant, report_code="students.enrollment_register",
            params={"campus_id": str(self.campus_b.id)},
        )
        self.assertEqual(len(result["rows"]), 1)
