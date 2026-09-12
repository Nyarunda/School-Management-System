from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import AcademicLevel, AcademicYear
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import FeeCategory, FeeItem, FinanceSetup, NumberSeries


class FinanceApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.user = User.objects.create_user(username="bursar-api", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a,
            name="Finance administrator",
            permissions=[
                "finance.setup.view",
                "finance.setup.manage",
                "finance.fee_structure.view",
                "finance.fee_structure.create",
                "finance.fee_structure.edit",
                "finance.fee_structure.approve",
                "finance.invoice.create",
                "finance.invoice.view",
                "finance.invoice.issue",
                "finance.credit_note.create",
                "finance.student_account.view",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        self.year = AcademicYear.objects.create(
            tenant=self.school_a,
            name="2026",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        self.level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.category = FeeCategory.objects.create(tenant=self.school_a, name="Tuition", code="TUITION")
        self.item = FeeItem.objects.create(
            tenant=self.school_a,
            category=self.category,
            name="Tuition fee",
            code="TUITION",
        )
        self.student = Student.objects.create(
            tenant=self.school_a,
            admission_number="ADM-001",
            first_name="Amina",
            last_name="Otieno",
        )
        self.foreign_student = Student.objects.create(
            tenant=self.school_b,
            admission_number="ADM-001",
            first_name="Peter",
            last_name="Kamau",
        )
        NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-")
        NumberSeries.objects.create(tenant=self.school_a, document_type="CREDIT_NOTE", prefix="CRN-2026-")
        self.client.force_authenticate(self.user)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_finance_flow_uses_business_actions_and_student_summary(self):
        setup_response = self.client.get("/api/v1/finance/setup/", **self.headers())
        self.assertEqual(setup_response.status_code, 200)

        setup_response = self.client.patch(
            "/api/v1/finance/setup/",
            {"currency": "KES"},
            format="json",
            **self.headers(),
        )
        self.assertEqual(setup_response.status_code, 200)
        self.assertEqual(setup_response.data["currency"], "KES")

        structure_response = self.client.post(
            "/api/v1/finance/fee-structures/",
            {"name": "Grade 8 2026", "academic_year": str(self.year.id), "academic_level": str(self.level.id)},
            format="json",
            **self.headers(),
        )
        self.assertEqual(structure_response.status_code, 201)
        structure_id = structure_response.data["id"]

        line_response = self.client.post(
            f"/api/v1/finance/fee-structures/{structure_id}/lines/",
            {"fee_item": str(self.item.id), "amount": "50000.00"},
            format="json",
            **self.headers(),
        )
        self.assertEqual(line_response.status_code, 201)

        approve_response = self.client.post(
            f"/api/v1/finance/fee-structures/{structure_id}/approve/",
            format="json",
            **self.headers(),
        )
        self.assertEqual(approve_response.status_code, 200)

        assignment_response = self.client.post(
            "/api/v1/finance/student-fee-assignments/",
            {"student": str(self.student.id), "fee_structure": structure_id},
            format="json",
            **self.headers(),
        )
        self.assertEqual(assignment_response.status_code, 201)
        assignment_id = assignment_response.data["id"]

        invoice_response = self.client.post(
            f"/api/v1/finance/student-fee-assignments/{assignment_id}/generate-invoice/",
            format="json",
            **self.headers(),
        )
        self.assertEqual(invoice_response.status_code, 201)
        invoice_id = invoice_response.data["id"]
        self.assertEqual(invoice_response.data["lines"][0]["unit_amount"], "50000.00")

        issue_response = self.client.post(
            f"/api/v1/finance/invoices/{invoice_id}/issue/",
            format="json",
            **self.headers(),
        )
        self.assertEqual(issue_response.status_code, 200)
        self.assertEqual(issue_response.data["status"], "ISSUED")

        summary_response = self.client.get(
            f"/api/v1/finance/students/{self.student.id}/finance/",
            **self.headers(),
        )
        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(summary_response.data["balance"], Decimal("50000.00"))
        self.assertEqual(len(summary_response.data["invoices"]), 1)

    def test_finance_api_requires_tenant_and_rejects_foreign_student(self):
        self.assertEqual(self.client.get("/api/v1/finance/fee-structures/").status_code, 404)

        response = self.client.get(
            f"/api/v1/finance/students/{self.foreign_student.id}/finance/",
            **self.headers(),
        )
        self.assertEqual(response.status_code, 404)

    def test_finance_api_permission_is_independent_of_role_name(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        response = self.client.get("/api/v1/finance/fee-structures/", **self.headers())

        self.assertEqual(response.status_code, 403)