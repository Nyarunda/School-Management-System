from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.academics.models import AcademicLevel, AcademicYear
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import FeeCategory, FeeItem, FinanceSetup, NumberSeries
from .services import add_fee_structure_line, approve_fee_structure, create_fee_structure


class FinanceSetupTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.user = User.objects.create_user(username="bursar", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a,
            name="Bursar",
            permissions=[
                "finance.fee_structure.create",
                "finance.fee_structure.edit",
                "finance.fee_structure.approve",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        self.year_a = AcademicYear.objects.create(
            tenant=self.school_a,
            name="2026",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        self.level_a = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.category_a = FeeCategory.objects.create(tenant=self.school_a, name="Tuition", code="TUITION")
        self.item_a = FeeItem.objects.create(
            tenant=self.school_a,
            category=self.category_a,
            name="Tuition fee",
            code="TUITION",
        )

    def test_finance_setup_and_number_series_are_tenant_configurable(self):
        setup = FinanceSetup.objects.create(tenant=self.school_a, currency="KES")
        series = NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-", padding=6)

        self.assertEqual(setup.currency, "KES")
        self.assertEqual(series.preview(), "INV-2026-000001")

    def test_fee_structure_requires_permission_and_same_tenant_setup(self):
        structure = create_fee_structure(
            user=self.user,
            tenant=self.school_a,
            name="Grade 8 2026",
            academic_year=self.year_a,
            academic_level=self.level_a,
        )

        self.assertEqual(structure.tenant, self.school_a)
        other_year = AcademicYear.objects.create(
            tenant=self.school_b,
            name="2026",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        with self.assertRaises(ValidationError):
            create_fee_structure(
                user=self.user,
                tenant=self.school_a,
                name="Cross-tenant structure",
                academic_year=other_year,
                academic_level=self.level_a,
            )

    def test_fee_structure_must_have_lines_before_approval_and_is_immutable_after(self):
        structure = create_fee_structure(
            user=self.user,
            tenant=self.school_a,
            name="Grade 8 2026",
            academic_year=self.year_a,
            academic_level=self.level_a,
        )
        with self.assertRaises(ValidationError):
            approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)

        line = add_fee_structure_line(
            user=self.user,
            tenant=self.school_a,
            fee_structure=structure,
            fee_item=self.item_a,
            amount=Decimal("25000.00"),
        )
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)
        structure.refresh_from_db()
        self.assertTrue(structure.is_approved)

        with self.assertRaises(ValidationError):
            add_fee_structure_line(
                user=self.user,
                tenant=self.school_a,
                fee_structure=structure,
                fee_item=self.item_a,
                amount=Decimal("1000.00"),
            )
        self.assertEqual(line.amount, Decimal("25000.00"))

    def test_missing_permission_is_rejected(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            create_fee_structure(
                user=self.user,
                tenant=self.school_a,
                name="Unauthorized",
                academic_year=self.year_a,
                academic_level=self.level_a,
            )