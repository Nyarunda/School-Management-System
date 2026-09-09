from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.tenancy.models import Membership, Role, Tenant, User

from .models import AcademicLevel, AcademicYear


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
