from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, EnrollmentStatus, StudentEnrollment, Subject, TeacherAssignment
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import AttendanceSession


class AttendanceApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")

        self.teacher = User.objects.create_user(username="teacher", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a, name="Teacher", permissions=["attendance.session.manage", "attendance.record.view"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.role)
        self.client.force_authenticate(self.teacher)

        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        today = timezone.now().date()
        self.year = AcademicYear.objects.create(
            tenant=self.school_a, name="Current", starts_on=today - timedelta(days=400), ends_on=today + timedelta(days=400),
        )
        self.level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_group = ClassGroup.objects.create(tenant=self.school_a, name="Grade 8 East", code="G8-E", academic_level=self.level, campus=self.campus)
        self.subject = Subject.objects.create(tenant=self.school_a, name="Math", code="MATH")
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.class_group, subject=self.subject)

        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        StudentEnrollment.objects.create(
            tenant=self.school_a, student=self.student, academic_year=self.year, academic_level=self.level,
            class_group=self.class_group, campus=self.campus, status=EnrollmentStatus.ACTIVE,
        )
        # An instructional weekday close to "now" -- the attendance summary
        # view windows on real time, so fixtures must stay time-relative
        # rather than a hardcoded historical date (matching the rest of
        # this project's time-window tests, e.g. apps/activity/test_durable_work.py).
        if today.isoweekday() >= 6:
            today -= timedelta(days=today.isoweekday() - 5)
        self.session_date = today.isoformat()

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_missing_tenant_header_is_rejected(self):
        response = self.client.post("/api/v1/attendance/sessions/open/", {"class_group": str(self.class_group.id), "session_date": self.session_date}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_open_session_returns_roster_and_records(self):
        response = self.client.post(
            "/api/v1/attendance/sessions/open/",
            {"class_group": str(self.class_group.id), "session_date": self.session_date},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["roster"]), 1)
        self.assertEqual(response.data["roster"][0]["admission_number"], "ADM-001")
        self.assertEqual(response.data["records"], [])

    def test_unassigned_teacher_is_forbidden(self):
        other_teacher = User.objects.create_user(username="other-teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=other_teacher, role=self.role)
        self.client.force_authenticate(other_teacher)
        response = self.client.post(
            "/api/v1/attendance/sessions/open/",
            {"class_group": str(self.class_group.id), "session_date": self.session_date},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_submit_and_correct_records(self):
        open_response = self.client.post(
            "/api/v1/attendance/sessions/open/",
            {"class_group": str(self.class_group.id), "session_date": self.session_date},
            format="json", **self.headers(),
        )
        session_id = open_response.data["session"]["id"]

        submit_response = self.client.post(
            f"/api/v1/attendance/sessions/{session_id}/records/",
            {"entries": [{"student": str(self.student.id), "status": "PRESENT"}]},
            format="json", **self.headers(),
        )
        self.assertEqual(submit_response.status_code, 200)
        self.assertEqual(submit_response.data[0]["status"], "PRESENT")

        correction_response = self.client.post(
            f"/api/v1/attendance/sessions/{session_id}/records/",
            {"entries": [{"student": str(self.student.id), "status": "LATE", "remarks": "Bus delay"}]},
            format="json", **self.headers(),
        )
        self.assertEqual(correction_response.status_code, 200)
        self.assertEqual(correction_response.data[0]["status"], "LATE")

    def test_session_not_in_this_tenant_is_not_found(self):
        foreign_campus = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        foreign_year = AcademicYear.objects.create(tenant=self.school_b, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        foreign_level = AcademicLevel.objects.create(tenant=self.school_b, name="Grade 8", code="G8", sequence=8)
        foreign_class = ClassGroup.objects.create(tenant=self.school_b, name="Grade 8", code="G8", academic_level=foreign_level, campus=foreign_campus)
        foreign_session = AttendanceSession.objects.create(
            tenant=self.school_b, class_group=foreign_class, session_date=date(2026, 3, 2), opened_by=self.teacher,
        )
        response = self.client.get(f"/api/v1/attendance/sessions/{foreign_session.id}/", **self.headers())
        self.assertEqual(response.status_code, 404)

    def test_session_list_is_paginated_and_filterable(self):
        base_date = date.fromisoformat(self.session_date)
        for offset in (0, 1, 2):
            self.client.post(
                "/api/v1/attendance/sessions/open/",
                {
                    "class_group": str(self.class_group.id),
                    "session_date": (base_date + timedelta(days=offset)).isoformat(),
                    "force": True,
                },
                format="json", **self.headers(),
            )
        response = self.client.get(f"/api/v1/attendance/sessions/?class_group={self.class_group.id}", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)

    def test_student_summary_is_bounded_and_includes_status_counts(self):
        open_response = self.client.post(
            "/api/v1/attendance/sessions/open/",
            {"class_group": str(self.class_group.id), "session_date": self.session_date},
            format="json", **self.headers(),
        )
        session_id = open_response.data["session"]["id"]
        self.client.post(
            f"/api/v1/attendance/sessions/{session_id}/records/",
            {"entries": [{"student": str(self.student.id), "status": "PRESENT"}]},
            format="json", **self.headers(),
        )
        response = self.client.get(f"/api/v1/attendance/students/{self.student.id}/summary/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status_counts"]["PRESENT"], 1)
        self.assertIn("recent_records", response.data)

    def test_student_record_history_is_paginated(self):
        response = self.client.get(f"/api/v1/attendance/students/{self.student.id}/records/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)
