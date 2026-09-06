from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, EnrollmentStatus, StudentEnrollment, Subject, TeacherAssignment, Term
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import AssessmentType, GradingScheme


class AssessmentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")

        self.teacher = User.objects.create_user(username="teacher", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a, name="Teacher",
            permissions=[
                "assessment.manage", "assessment.marks.manage", "assessment.result.amend",
                "assessment.record.view", "assessment.approve", "assessment.publish",
                "assessment.setup.manage", "assessment.setup.view",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.role)
        self.client.force_authenticate(self.teacher)

        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        today = timezone.now().date()
        self.year = AcademicYear.objects.create(
            tenant=self.school_a, name="Current", starts_on=today - timedelta(days=400), ends_on=today + timedelta(days=400),
        )
        self.term = Term.objects.create(
            tenant=self.school_a, academic_year=self.year, name="Term 1",
            starts_on=today - timedelta(days=400), ends_on=today + timedelta(days=400), sequence=1,
        )
        self.level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_group = ClassGroup.objects.create(tenant=self.school_a, name="Grade 8 East", code="G8-E", academic_level=self.level, campus=self.campus)
        self.subject = Subject.objects.create(tenant=self.school_a, name="Math", code="MATH")
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.class_group, subject=self.subject)
        self.assessment_type = AssessmentType.objects.create(tenant=self.school_a, name="CAT", code="CAT")

        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        StudentEnrollment.objects.create(
            tenant=self.school_a, student=self.student, academic_year=self.year, academic_level=self.level,
            class_group=self.class_group, campus=self.campus, status=EnrollmentStatus.ACTIVE,
        )
        self.scheduled_date = today.isoformat()

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def open_assessment(self, name="CAT 1"):
        return self.client.post(
            "/api/v1/assessments/assessments/open/",
            {
                "term": str(self.term.id), "class_group": str(self.class_group.id), "subject": str(self.subject.id),
                "assessment_type": str(self.assessment_type.id), "name": name, "max_marks": "100.00",
                "scheduled_date": self.scheduled_date,
            },
            format="json", **self.headers(),
        )

    def test_missing_tenant_header_is_rejected(self):
        response = self.client.post("/api/v1/assessments/assessments/open/", {}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_open_assessment_returns_roster_snapshot(self):
        response = self.open_assessment()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["mark_status"], "NOT_MARKED")

    def test_full_lifecycle_via_http(self):
        open_response = self.open_assessment()
        assessment_id = open_response.data["assessment"]["id"]

        marks_response = self.client.post(
            f"/api/v1/assessments/assessments/{assessment_id}/marks/",
            {"entries": [{"student": str(self.student.id), "mark_status": "SCORED", "score": "88.00"}]},
            format="json", **self.headers(),
        )
        self.assertEqual(marks_response.status_code, 200)
        self.assertEqual(marks_response.data[0]["mark_status"], "SCORED")

        submit_response = self.client.post(f"/api/v1/assessments/assessments/{assessment_id}/submit/", **self.headers())
        self.assertEqual(submit_response.status_code, 200)
        self.assertEqual(submit_response.data["status"], "SUBMITTED")

        approve_response = self.client.post(f"/api/v1/assessments/assessments/{assessment_id}/approve/", **self.headers())
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.data["status"], "APPROVED")

        publish_response = self.client.post(f"/api/v1/assessments/assessments/{assessment_id}/publish/", **self.headers())
        self.assertEqual(publish_response.status_code, 200)
        self.assertEqual(publish_response.data["status"], "PUBLISHED")

        # SUBMITTED/APPROVED are locked; a corrected mark once PUBLISHED needs
        # assessment.result.amend, which this role also holds.
        amend_response = self.client.post(
            f"/api/v1/assessments/assessments/{assessment_id}/marks/",
            {"entries": [{"student": str(self.student.id), "mark_status": "SCORED", "score": "91.00"}]},
            format="json", **self.headers(),
        )
        self.assertEqual(amend_response.status_code, 200)

    def test_marks_are_rejected_while_submitted(self):
        open_response = self.open_assessment()
        assessment_id = open_response.data["assessment"]["id"]
        self.client.post(
            f"/api/v1/assessments/assessments/{assessment_id}/marks/",
            {"entries": [{"student": str(self.student.id), "mark_status": "SCORED", "score": "88.00"}]},
            format="json", **self.headers(),
        )
        self.client.post(f"/api/v1/assessments/assessments/{assessment_id}/submit/", **self.headers())

        response = self.client.post(
            f"/api/v1/assessments/assessments/{assessment_id}/marks/",
            {"entries": [{"student": str(self.student.id), "mark_status": "SCORED", "score": "99.00"}]},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_reject_sends_back_to_draft(self):
        open_response = self.open_assessment()
        assessment_id = open_response.data["assessment"]["id"]
        self.client.post(
            f"/api/v1/assessments/assessments/{assessment_id}/marks/",
            {"entries": [{"student": str(self.student.id), "mark_status": "SCORED", "score": "88.00"}]},
            format="json", **self.headers(),
        )
        self.client.post(f"/api/v1/assessments/assessments/{assessment_id}/submit/", **self.headers())

        reject_response = self.client.post(
            f"/api/v1/assessments/assessments/{assessment_id}/reject/", {"reason": "Needs review"},
            format="json", **self.headers(),
        )
        self.assertEqual(reject_response.status_code, 200)
        self.assertEqual(reject_response.data["status"], "DRAFT")

    def test_grading_scheme_and_band_setup(self):
        scheme_response = self.client.post(
            "/api/v1/assessments/grading-schemes/",
            {"name": "Standard", "academic_level": str(self.level.id)},
            format="json", **self.headers(),
        )
        self.assertEqual(scheme_response.status_code, 201)
        scheme_id = scheme_response.data["id"]

        band_response = self.client.post(
            f"/api/v1/assessments/grading-schemes/{scheme_id}/bands/",
            {"grade_label": "A", "min_percentage": "80", "max_percentage": "100"},
            format="json", **self.headers(),
        )
        self.assertEqual(band_response.status_code, 201)

    def test_assessment_list_is_paginated_and_filterable(self):
        self.open_assessment(name="CAT 1")
        self.open_assessment(name="CAT 2")
        response = self.client.get(f"/api/v1/assessments/assessments/?class_group={self.class_group.id}", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_student_summary_is_bounded(self):
        open_response = self.open_assessment()
        assessment_id = open_response.data["assessment"]["id"]
        self.client.post(
            f"/api/v1/assessments/assessments/{assessment_id}/marks/",
            {"entries": [{"student": str(self.student.id), "mark_status": "SCORED", "score": "80.00"}]},
            format="json", **self.headers(),
        )
        response = self.client.get(f"/api/v1/assessments/students/{self.student.id}/summary/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["mark_status_counts"]["SCORED"], 1)
        self.assertEqual(Decimal(str(response.data["average_percentage"])), Decimal("80.00"))
        self.assertIn("recent_results", response.data)

    def test_student_result_history_is_paginated(self):
        response = self.client.get(f"/api/v1/assessments/students/{self.student.id}/results/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)
