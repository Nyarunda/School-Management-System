from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import AcademicLevel, AcademicYear, ClassGroup, Subject, TeacherAssignment


class AcademicCatalogueApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.user = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(tenant=self.school_a, name="Bursar", permissions=["academics.setup.view"])
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)

        self.year_older = AcademicYear.objects.create(
            tenant=self.school_a, name="2025", starts_on=date(2025, 1, 1), ends_on=date(2025, 12, 31),
        )
        self.year_newer = AcademicYear.objects.create(
            tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31), is_current=True,
        )
        self.level_low = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 7", code="G7", sequence=7)
        self.level_high = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)

        AcademicYear.objects.create(tenant=self.school_b, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        AcademicLevel.objects.create(tenant=self.school_b, name="Grade 8", code="G8", sequence=8)

        self.client.force_authenticate(self.user)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_academic_years_are_tenant_scoped_and_ordered_newest_first(self):
        response = self.client.get("/api/v1/academics/academic-years/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["name"] for row in response.data["results"]], ["2026", "2025"])

    def test_academic_levels_are_tenant_scoped_and_ordered_by_sequence(self):
        response = self.client.get("/api/v1/academics/academic-levels/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["code"] for row in response.data["results"]], ["G7", "G8"])

    def test_academic_years_requires_tenant_context(self):
        response = self.client.get("/api/v1/academics/academic-years/")
        self.assertEqual(response.status_code, 404)

    def test_academic_years_requires_permission(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])
        response = self.client.get("/api/v1/academics/academic-years/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_academic_levels_requires_permission(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])
        response = self.client.get("/api/v1/academics/academic-levels/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_module_disabled_blocks_access_even_with_permission(self):
        from apps.platform.services import set_module_override

        set_module_override(tenant=self.school_a, module_code="academics", is_enabled=False)
        response = self.client.get("/api/v1/academics/academic-years/", **self.headers())
        self.assertEqual(response.status_code, 403)


class ClassGroupCatalogueApiTests(TestCase):
    """ACADEMIC-GAP-01: the class-group catalogue exists solely so
    Attendance's Open Register action has a legitimate class_group to
    submit, so it is gated by attendance.session.manage (not
    academics.setup.view) and filtered to exactly what
    apps.attendance.services._require_class_authorization would accept.
    """

    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")

        self.main_campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.annex_campus = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")
        self.level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.subject = Subject.objects.create(tenant=self.school_a, name="Math", code="MATH")

        self.assigned_class = ClassGroup.objects.create(
            tenant=self.school_a, name="Grade 8 East", code="G8-E", academic_level=self.level, campus=self.main_campus,
        )
        self.unassigned_same_campus_class = ClassGroup.objects.create(
            tenant=self.school_a, name="Grade 8 West", code="G8-W", academic_level=self.level, campus=self.main_campus,
        )
        self.annex_class = ClassGroup.objects.create(
            tenant=self.school_a, name="Grade 8 Annex", code="G8-A", academic_level=self.level, campus=self.annex_campus,
        )

        self.teacher = User.objects.create_user(username="teacher", password="secret")
        self.teacher_role = Role.objects.create(
            tenant=self.school_a, name="Teacher", permissions=["attendance.session.manage"],
        )
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.assigned_class, subject=self.subject)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_campus_scoped_teacher_sees_only_assigned_classes_in_their_campus(self):
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.teacher_role, campus=self.main_campus)
        self.client.force_authenticate(self.teacher)
        response = self.client.get("/api/v1/academics/class-groups/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["code"] for row in response.data["results"]], ["G8-E"])

    def test_campus_scoped_teacher_cannot_see_another_campus_even_if_assigned(self):
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.annex_class, subject=self.subject)
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.teacher_role, campus=self.main_campus)
        self.client.force_authenticate(self.teacher)
        response = self.client.get("/api/v1/academics/class-groups/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["code"] for row in response.data["results"]], ["G8-E"])

    def test_any_class_override_sees_every_class_in_campus_without_assignment(self):
        override_role = Role.objects.create(
            tenant=self.school_a, name="Head of Campus", permissions=["attendance.session.manage", "attendance.any_class"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=override_role, campus=self.main_campus)
        self.client.force_authenticate(self.teacher)
        response = self.client.get("/api/v1/academics/class-groups/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(row["code"] for row in response.data["results"]), ["G8-E", "G8-W"])

    def test_tenant_wide_membership_sees_assigned_classes_across_campuses(self):
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.annex_class, subject=self.subject)
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.teacher_role, campus=None)
        self.client.force_authenticate(self.teacher)
        response = self.client.get("/api/v1/academics/class-groups/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(row["code"] for row in response.data["results"]), ["G8-A", "G8-E"])

    def test_unassigned_teacher_sees_no_classes(self):
        other_teacher = User.objects.create_user(username="other-teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=other_teacher, role=self.teacher_role, campus=self.main_campus)
        self.client.force_authenticate(other_teacher)
        response = self.client.get("/api/v1/academics/class-groups/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])

    def test_class_groups_are_tenant_isolated(self):
        foreign_campus = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        foreign_level = AcademicLevel.objects.create(tenant=self.school_b, name="Grade 8", code="G8", sequence=8)
        ClassGroup.objects.create(tenant=self.school_b, name="Grade 8", code="G8", academic_level=foreign_level, campus=foreign_campus)
        override_role = Role.objects.create(
            tenant=self.school_a, name="Head of Campus", permissions=["attendance.session.manage", "attendance.any_class"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=override_role, campus=None)
        self.client.force_authenticate(self.teacher)
        response = self.client.get("/api/v1/academics/class-groups/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(row["code"] for row in response.data["results"]), ["G8-A", "G8-E", "G8-W"])

    def test_requires_permission(self):
        role = Role.objects.create(tenant=self.school_a, name="No Access", permissions=[])
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=role, campus=self.main_campus)
        self.client.force_authenticate(self.teacher)
        response = self.client.get("/api/v1/academics/class-groups/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_requires_tenant_context(self):
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.teacher_role, campus=self.main_campus)
        self.client.force_authenticate(self.teacher)
        response = self.client.get("/api/v1/academics/class-groups/")
        self.assertEqual(response.status_code, 404)

    def test_attendance_module_disabled_blocks_access_even_with_permission(self):
        from apps.platform.services import set_module_override

        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.teacher_role, campus=self.main_campus)
        self.client.force_authenticate(self.teacher)
        set_module_override(tenant=self.school_a, module_code="attendance", is_enabled=False)
        response = self.client.get("/api/v1/academics/class-groups/", **self.headers())
        self.assertEqual(response.status_code, 403)
