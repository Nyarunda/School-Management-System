from datetime import date, timedelta, time

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, Subject, TeacherAssignment, Term
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Period


class TimetableApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a, name="Coordinator",
            permissions=["timetable.manage", "timetable.setup.manage", "timetable.setup.view", "timetable.record.view"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.role)
        self.client.force_authenticate(self.admin)

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
        self.teacher = User.objects.create_user(username="teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.role)
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.class_group, subject=self.subject)
        self.period = Period.objects.create(tenant=self.school_a, name="Period 1", sequence=1, starts_at=time(8, 0), ends_at=time(8, 40))
        self.monday = 1

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def create_entry(self, **overrides):
        payload = {
            "term": str(self.term.id), "class_group": str(self.class_group.id), "subject": str(self.subject.id),
            "teacher": str(self.teacher.id), "period": str(self.period.id), "day_of_week": self.monday, "room": "Lab 1",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/timetable/entries/create/", payload, format="json", **self.headers())

    def test_missing_tenant_header_is_rejected(self):
        response = self.client.post("/api/v1/timetable/entries/create/", {}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_period_crud(self):
        create_response = self.client.post(
            "/api/v1/timetable/periods/",
            {"name": "Period 2", "sequence": 2, "starts_at": "08:40:00", "ends_at": "09:20:00"},
            format="json", **self.headers(),
        )
        self.assertEqual(create_response.status_code, 201)
        period_id = create_response.data["id"]

        update_response = self.client.patch(
            f"/api/v1/timetable/periods/{period_id}/", {"name": "Renamed"}, format="json", **self.headers(),
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data["name"], "Renamed")

        delete_response = self.client.delete(f"/api/v1/timetable/periods/{period_id}/", **self.headers())
        self.assertEqual(delete_response.status_code, 204)

    def test_create_entry_then_conflict_then_update_then_delete(self):
        create_response = self.create_entry()
        self.assertEqual(create_response.status_code, 200)
        entry_id = create_response.data["id"]

        conflict_response = self.create_entry(room="Lab 2")
        self.assertEqual(conflict_response.status_code, 400)

        update_response = self.client.patch(
            f"/api/v1/timetable/entries/{entry_id}/", {"room": "Lab 3"}, format="json", **self.headers(),
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data["room"], "Lab 3")

        delete_response = self.client.delete(f"/api/v1/timetable/entries/{entry_id}/", **self.headers())
        self.assertEqual(delete_response.status_code, 204)

    def test_class_schedule_and_teacher_schedule_grids(self):
        self.create_entry()
        class_response = self.client.get(
            f"/api/v1/timetable/classes/{self.class_group.id}/schedule/?term={self.term.id}", **self.headers(),
        )
        self.assertEqual(class_response.status_code, 200)
        self.assertEqual(len(class_response.data), 1)

        teacher_response = self.client.get(
            f"/api/v1/timetable/teachers/{self.teacher.id}/schedule/?term={self.term.id}", **self.headers(),
        )
        self.assertEqual(teacher_response.status_code, 200)
        self.assertEqual(len(teacher_response.data), 1)

    def test_entry_list_is_paginated_and_filterable(self):
        self.create_entry()
        response = self.client.get(f"/api/v1/timetable/entries/?class_group={self.class_group.id}", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_teacher_not_assigned_is_a_clean_400(self):
        other_teacher = User.objects.create_user(username="other-teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=other_teacher, role=self.role)
        response = self.create_entry(teacher=str(other_teacher.id))
        self.assertEqual(response.status_code, 400)
