from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.academics.models import AcademicLevel, AcademicYear
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import FeeCategory, FeeItem, FinanceSetup, Invoice, NumberSeries
from .selectors import student_balance
from .services import (
    add_fee_structure_line,
    approve_fee_structure,
    assign_fee_structure,
    create_fee_structure,
    generate_invoice,
    issue_credit_note,
    issue_invoice,
)
from apps.students.models import Student


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
                "finance.invoice.create",
                "finance.invoice.issue",
                "finance.credit_note.create",
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
        self.student_a = Student.objects.create(
            tenant=self.school_a,
            admission_number="ADM-001",
            first_name="Amina",
            last_name="Otieno",
        )
        NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="CREDIT_NOTE", prefix="CRN-2026-", padding=6)

    def test_finance_setup_and_number_series_are_tenant_configurable(self):
        setup = FinanceSetup.objects.create(tenant=self.school_a, currency="KES")
        series = NumberSeries.objects.get(tenant=self.school_a, document_type="INVOICE")

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

    def _approved_structure(self):
        structure = create_fee_structure(
            user=self.user,
            tenant=self.school_a,
            name="Grade 8 2026",
            academic_year=self.year_a,
            academic_level=self.level_a,
        )
        add_fee_structure_line(
            user=self.user,
            tenant=self.school_a,
            fee_structure=structure,
            fee_item=self.item_a,
            amount=Decimal("50000.00"),
        )
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)
        return structure

    def test_invoice_lines_snapshot_setup_amounts(self):
        structure = self._approved_structure()
        assignment = assign_fee_structure(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            fee_structure=structure,
        )
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)

        structure.lines.update(amount=Decimal("28000.00"))
        line = invoice.lines.get()
        self.assertEqual(line.unit_amount, Decimal("50000.00"))
        self.assertEqual(invoice.total, Decimal("50000.00"))

    def test_invoice_generation_is_idempotent(self):
        structure = self._approved_structure()
        assignment = assign_fee_structure(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            fee_structure=structure,
        )

        first = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        second = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)

        self.assertEqual(first.id, second.id)
        self.assertEqual(Invoice.objects.filter(assignment=assignment).count(), 1)

    def test_issue_and_credit_note_update_calculated_student_balance(self):
        structure = self._approved_structure()
        assignment = assign_fee_structure(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            fee_structure=structure,
        )
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        issue_invoice(user=self.user, tenant=self.school_a, invoice=invoice)
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student_a), Decimal("50000.00"))

        issue_credit_note(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            invoice=invoice,
            amount=Decimal("5000.00"),
            reason="Approved bursary",
        )
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student_a), Decimal("45000.00"))

    def test_invoice_issue_requires_permission(self):
        structure = self._approved_structure()
        assignment = assign_fee_structure(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            fee_structure=structure,
        )
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        self.role.permissions = ["finance.invoice.create"]
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            issue_invoice(user=self.user, tenant=self.school_a, invoice=invoice)