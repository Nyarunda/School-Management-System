import uuid
from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import AcademicLevel, AcademicYear
from apps.activity.models import ActivityEvent
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import FeeCategory, FeeItem, FinanceSetup, NumberSeries, PaymentMethod


class FinanceApiTests(TestCase):
    def test_domain_validation_uses_shared_handler(self):
        from .models import FeeStructure
        structure = FeeStructure.objects.create(
            tenant=self.school_a, name="Empty", academic_year=self.year, academic_level=self.level,
        )
        response = self.client.post(
            f"/api/v1/finance/fee-structures/{structure.id}/approve/", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"detail": ["A fee structure must have at least one line before approval"]})
        structure.refresh_from_db()
        self.assertFalse(structure.is_approved)

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
                "finance.payment.record",
                "finance.payment.view",
                "finance.payment.allocate",
                "finance.payment.reverse",
                "finance.allocation.reverse",
                "finance.reconciliation.view",
                "finance.reconciliation.ingest",
                "finance.reconciliation.match",
                "finance.reconciliation.ignore",
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
        NumberSeries.objects.create(tenant=self.school_a, document_type="RECEIPT", prefix="RCT-2026-")
        NumberSeries.objects.create(tenant=self.school_a, document_type="PAYMENT_REVERSAL", prefix="PRV-2026-")
        self.payment_method = PaymentMethod.objects.create(tenant=self.school_a, name="Bank transfer", code="BANK")
        self.client.force_authenticate(self.user)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_disabling_the_finance_module_blocks_access_even_with_permission(self):
        from apps.platform.services import set_module_override

        self.client.force_authenticate(self.user)
        set_module_override(tenant=self.school_a, module_code="finance", is_enabled=False)
        response = self.client.get("/api/v1/finance/setup/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_updating_finance_setup_records_one_activity_event_with_bounded_metadata(self):
        response = self.client.patch(
            "/api/v1/finance/setup/",
            {"currency": "KES", "configuration": {"secret_bank_account": "12345"}},
            format="json",
            **self.headers(),
        )
        self.assertEqual(response.status_code, 200)

        event = ActivityEvent.objects.get(action="finance_setup.updated")
        self.assertEqual(sorted(event.metadata["changed_fields"]), ["configuration", "currency"])
        self.assertNotIn("secret_bank_account", str(event.metadata))
        self.assertNotIn("KES", str(event.metadata))

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
        self.assertEqual(summary_response.data["summary"]["outstanding_balance"], Decimal("50000.00"))
        self.assertEqual(summary_response.data["summary"]["total_invoiced"], Decimal("50000.00"))
        self.assertEqual(len(summary_response.data["recent_invoices"]), 1)

    def test_invoice_list_query_shape_is_bounded(self):
        from .models import FeeStructure, Invoice, InvoiceLine, StudentFeeAssignment

        for index in range(3):
            structure = FeeStructure.objects.create(
                tenant=self.school_a, name=f"Structure {index}", academic_year=self.year, academic_level=self.level, is_approved=True,
            )
            assignment = StudentFeeAssignment.objects.create(tenant=self.school_a, student=self.student, fee_structure=structure)
            invoice = Invoice.objects.create(
                tenant=self.school_a, invoice_number=f"INV-2026-{index}", idempotency_key=f"idem-{index}",
                student=self.student, assignment=assignment, subtotal=Decimal("50000.00"), total=Decimal("50000.00"),
            )
            InvoiceLine.objects.create(
                tenant=self.school_a, invoice=invoice, fee_item=self.item, description="Tuition fee",
                unit_amount=Decimal("50000.00"), net_amount=Decimal("50000.00"),
            )

        # Milestone 22.3 permanent query-count regression coverage.
        with self.assertNumQueries(6):
            response = self.client.get("/api/v1/finance/invoices/", **self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)

    def test_payment_list_query_shape_is_bounded(self):
        for index in range(3):
            self.client.post(
                "/api/v1/finance/payments/",
                {
                    "student": str(self.student.id), "amount": "1000.00", "payment_method": str(self.payment_method.id),
                    "idempotency_key": f"payment-list-{index}",
                },
                format="json", **self.headers(),
            )

        with self.assertNumQueries(8):
            response = self.client.get("/api/v1/finance/payments/", **self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)

    def _issued_invoice(self, structure_name="Grade 8 2026"):
        structure_response = self.client.post(
            "/api/v1/finance/fee-structures/",
            {"name": structure_name, "academic_year": str(self.year.id), "academic_level": str(self.level.id)},
            format="json",
            **self.headers(),
        )
        structure_id = structure_response.data["id"]
        self.client.post(
            f"/api/v1/finance/fee-structures/{structure_id}/lines/",
            {"fee_item": str(self.item.id), "amount": "50000.00"},
            format="json",
            **self.headers(),
        )
        self.client.post(f"/api/v1/finance/fee-structures/{structure_id}/approve/", format="json", **self.headers())
        assignment_response = self.client.post(
            "/api/v1/finance/student-fee-assignments/",
            {"student": str(self.student.id), "fee_structure": structure_id},
            format="json",
            **self.headers(),
        )
        assignment_id = assignment_response.data["id"]
        invoice_response = self.client.post(
            f"/api/v1/finance/student-fee-assignments/{assignment_id}/generate-invoice/",
            format="json",
            **self.headers(),
        )
        invoice_id = invoice_response.data["id"]
        self.client.post(f"/api/v1/finance/invoices/{invoice_id}/issue/", format="json", **self.headers())
        return invoice_id

    def test_payment_record_allocate_and_reverse_flow(self):
        invoice_id = self._issued_invoice()

        payment_response = self.client.post(
            "/api/v1/finance/payments/",
            {
                "student": str(self.student.id),
                "payment_method": str(self.payment_method.id),
                "amount": "50000.00",
                "idempotency_key": "receipt-001",
            },
            format="json",
            **self.headers(),
        )
        self.assertEqual(payment_response.status_code, 201)
        payment_id = payment_response.data["id"]
        self.assertEqual(payment_response.data["unallocated_amount"], Decimal("50000.00"))
        self.assertIsNotNone(payment_response.data["receipt"])

        replay_response = self.client.post(
            "/api/v1/finance/payments/",
            {
                "student": str(self.student.id),
                "payment_method": str(self.payment_method.id),
                "amount": "50000.00",
                "idempotency_key": "receipt-001",
            },
            format="json",
            **self.headers(),
        )
        self.assertEqual(replay_response.status_code, 201)
        self.assertEqual(replay_response.data["id"], payment_id)

        allocate_response = self.client.post(
            f"/api/v1/finance/payments/{payment_id}/allocate/",
            {"invoice": invoice_id, "amount": "50000.00"},
            format="json",
            **self.headers(),
        )
        self.assertEqual(allocate_response.status_code, 201)
        allocation_id = allocate_response.data["id"]

        summary_response = self.client.get(f"/api/v1/finance/students/{self.student.id}/finance/", **self.headers())
        self.assertEqual(summary_response.data["summary"]["outstanding_balance"], Decimal("0.00"))
        self.assertEqual(summary_response.data["summary"]["total_paid"], Decimal("50000.00"))
        self.assertEqual(summary_response.data["summary"]["unapplied_cash"], Decimal("0.00"))
        self.assertEqual(len(summary_response.data["recent_payments"]), 1)

        overallocate_response = self.client.post(
            f"/api/v1/finance/payments/{payment_id}/allocate/",
            {"invoice": invoice_id, "amount": "1.00"},
            format="json",
            **self.headers(),
        )
        self.assertEqual(overallocate_response.status_code, 400)

        reverse_response = self.client.post(
            f"/api/v1/finance/payment-allocations/{allocation_id}/reverse/",
            {"amount": "50000.00", "reason": "Bounced cheque"},
            format="json",
            **self.headers(),
        )
        self.assertEqual(reverse_response.status_code, 201)

        summary_response = self.client.get(f"/api/v1/finance/students/{self.student.id}/finance/", **self.headers())
        summary = summary_response.data["summary"]
        self.assertEqual(summary["outstanding_balance"], Decimal("50000.00"))
        self.assertEqual(summary["total_paid"], Decimal("0.00"))
        self.assertEqual(summary["unapplied_cash"], Decimal("50000.00"))
        self.assertEqual(summary["outstanding_balance"], summary["total_invoiced"] - summary["total_credited"] - summary["total_paid"])

        ledger_response = self.client.get(f"/api/v1/finance/ledger-entries/?student={self.student.id}", **self.headers())
        self.assertEqual(ledger_response.status_code, 200)
        self.assertEqual(ledger_response.data["count"], 3)  # invoice debit, allocation credit, reversal debit

    def test_payment_detail_and_whole_payment_reversal_flow(self):
        invoice_a_id = self._issued_invoice("Grade 8 2026 A")
        invoice_b_id = self._issued_invoice("Grade 8 2026 B")

        payment_response = self.client.post(
            "/api/v1/finance/payments/",
            {"student": str(self.student.id), "payment_method": str(self.payment_method.id), "amount": "100000.00", "idempotency_key": "receipt-002"},
            format="json", **self.headers(),
        )
        payment_id = payment_response.data["id"]
        self.assertIsNone(payment_response.data["reversal"])
        self.assertEqual(payment_response.data["status"], "RECEIVED")

        for invoice_id in (invoice_a_id, invoice_b_id):
            response = self.client.post(
                f"/api/v1/finance/payments/{payment_id}/allocate/", {"invoice": invoice_id, "amount": "50000.00"},
                format="json", **self.headers(),
            )
            self.assertEqual(response.status_code, 201)

        detail_response = self.client.get(f"/api/v1/finance/payments/{payment_id}/", **self.headers())
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(len(detail_response.data["allocations"]), 2)
        self.assertEqual(detail_response.data["unallocated_amount"], Decimal("0.00"))

        reverse_response = self.client.post(
            f"/api/v1/finance/payments/{payment_id}/reverse/", {"reason": "Bounced cheque"},
            format="json", **self.headers(),
        )
        self.assertEqual(reverse_response.status_code, 201)
        self.assertTrue(reverse_response.data["reversal_number"])

        detail_response = self.client.get(f"/api/v1/finance/payments/{payment_id}/", **self.headers())
        self.assertEqual(detail_response.data["status"], "REVERSED")
        self.assertIsNotNone(detail_response.data["reversal"])
        self.assertEqual(detail_response.data["unallocated_amount"], Decimal("0.00"))

        summary_response = self.client.get(f"/api/v1/finance/students/{self.student.id}/finance/", **self.headers())
        self.assertEqual(summary_response.data["summary"]["outstanding_balance"], Decimal("100000.00"))

        # A reversed payment cannot be reversed again, nor allocated further.
        self.assertEqual(
            self.client.post(f"/api/v1/finance/payments/{payment_id}/reverse/", {"reason": "Again"}, format="json", **self.headers()).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(f"/api/v1/finance/payments/{payment_id}/allocate/", {"invoice": invoice_a_id, "amount": "1.00"}, format="json", **self.headers()).status_code,
            400,
        )

    def test_payment_reversal_malformed_id_and_missing_reason(self):
        response = self.client.get(f"/api/v1/finance/payments/{uuid.uuid4()}/", **self.headers())
        self.assertEqual(response.status_code, 404)

        response = self.client.get("/api/v1/finance/payments/not-a-uuid/", **self.headers())
        self.assertEqual(response.status_code, 404)

        payment_response = self.client.post(
            "/api/v1/finance/payments/",
            {"student": str(self.student.id), "payment_method": str(self.payment_method.id), "amount": "10.00", "idempotency_key": "receipt-003"},
            format="json", **self.headers(),
        )
        payment_id = payment_response.data["id"]

        response = self.client.post(f"/api/v1/finance/payments/{payment_id}/reverse/", {}, format="json", **self.headers())
        self.assertEqual(response.status_code, 400)

    def test_payment_reversal_requires_its_own_permission(self):
        payment_response = self.client.post(
            "/api/v1/finance/payments/",
            {"student": str(self.student.id), "payment_method": str(self.payment_method.id), "amount": "10.00", "idempotency_key": "receipt-004"},
            format="json", **self.headers(),
        )
        payment_id = payment_response.data["id"]
        self.role.permissions = [p for p in self.role.permissions if p != "finance.payment.reverse"]
        self.role.save(update_fields=["permissions"])

        response = self.client.post(f"/api/v1/finance/payments/{payment_id}/reverse/", {"reason": "Entered in error"}, format="json", **self.headers())
        self.assertEqual(response.status_code, 403)

    def _payment_payload(self, **overrides):
        payload = {
            "student": str(self.student.id),
            "payment_method": str(self.payment_method.id),
            "amount": "10.00",
            "idempotency_key": "x",
        }
        payload.update(overrides)
        return payload

    def test_payment_malformed_uuid_format_is_a_400_not_a_404(self):
        # A syntactically invalid identifier is a client input error (400),
        # distinct from a well-formed identifier that just doesn't resolve
        # to a real tenant-scoped record (404, via resolve_tenant_object).
        response = self.client.post(
            "/api/v1/finance/payments/", self._payment_payload(student="not-a-uuid"), format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_payment_well_formed_but_nonexistent_identifier_is_a_404(self):
        response = self.client.post(
            "/api/v1/finance/payments/", self._payment_payload(student=str(uuid.uuid4())), format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 404)

    def test_payment_amount_rejects_non_numeric_nan_infinity_and_bad_precision(self):
        for bad_amount in ("not-a-number", "NaN", "Infinity", "-Infinity", "10.999", "0", "-5.00"):
            with self.subTest(amount=bad_amount):
                response = self.client.post(
                    "/api/v1/finance/payments/", self._payment_payload(amount=bad_amount, idempotency_key=f"x-{bad_amount}"),
                    format="json", **self.headers(),
                )
                self.assertEqual(response.status_code, 400, f"amount={bad_amount!r} should be rejected, got {response.status_code}")

    def test_payment_missing_required_field_is_rejected(self):
        payload = self._payment_payload()
        del payload["idempotency_key"]
        response = self.client.post("/api/v1/finance/payments/", payload, format="json", **self.headers())
        self.assertEqual(response.status_code, 400)

    def test_payment_list_query_param_malformed_identifier_is_a_404(self):
        response = self.client.get("/api/v1/finance/payments/?student=not-a-uuid", **self.headers())
        self.assertEqual(response.status_code, 404)

    def test_payment_permission_is_independent_per_action(self):
        self.role.permissions = ["finance.payment.view"]
        self.role.save(update_fields=["permissions"])

        response = self.client.post(
            "/api/v1/finance/payments/",
            {"student": str(self.student.id), "payment_method": str(self.payment_method.id), "amount": "10", "idempotency_key": "x"},
            format="json",
            **self.headers(),
        )
        self.assertEqual(response.status_code, 403)

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

    def _incoming_payment_payload(self, **overrides):
        payload = {
            "payment_method": str(self.payment_method.id),
            "amount": "10.00",
            "external_reference": "",
            "external_transaction_id": "bank-001",
        }
        payload.update(overrides)
        return payload

    def test_incoming_payment_ingest_auto_match_and_auto_allocate_flow(self):
        invoice_id = self._issued_invoice("Grade 8 2026 Recon")

        response = self.client.post(
            "/api/v1/finance/incoming-payments/",
            self._incoming_payment_payload(amount="50000.00", external_reference=f"BANK/{self.student.admission_number}/JAN", external_transaction_id="bank-recon-1"),
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "MATCHED")
        self.assertIsNotNone(response.data["matched_payment"])

        payment_response = self.client.get(f"/api/v1/finance/payments/{response.data['matched_payment']}/", **self.headers())
        self.assertEqual(len(payment_response.data["allocations"]), 1)
        self.assertEqual(str(payment_response.data["allocations"][0]["invoice"]), invoice_id)

    def test_incoming_payment_manual_match_flow(self):
        ingest_response = self.client.post(
            "/api/v1/finance/incoming-payments/",
            self._incoming_payment_payload(external_transaction_id="bank-recon-2"),
            format="json", **self.headers(),
        )
        self.assertEqual(ingest_response.status_code, 201)
        self.assertEqual(ingest_response.data["status"], "UNMATCHED")
        incoming_id = ingest_response.data["id"]

        match_response = self.client.post(
            f"/api/v1/finance/incoming-payments/{incoming_id}/match/",
            {"student": str(self.student.id)}, format="json", **self.headers(),
        )
        self.assertEqual(match_response.status_code, 200)
        self.assertEqual(match_response.data["status"], "MATCHED")

    def test_incoming_payment_ignore_flow(self):
        ingest_response = self.client.post(
            "/api/v1/finance/incoming-payments/",
            self._incoming_payment_payload(external_transaction_id="bank-recon-3"),
            format="json", **self.headers(),
        )
        incoming_id = ingest_response.data["id"]

        ignore_response = self.client.post(
            f"/api/v1/finance/incoming-payments/{incoming_id}/ignore/",
            {"reason": "Bank fee"}, format="json", **self.headers(),
        )
        self.assertEqual(ignore_response.status_code, 200)
        self.assertEqual(ignore_response.data["status"], "IGNORED")
        self.assertEqual(ignore_response.data["ignored_reason"], "Bank fee")

    def test_incoming_payment_status_and_received_after_filtering(self):
        self.client.post("/api/v1/finance/incoming-payments/", self._incoming_payment_payload(external_transaction_id="bank-recon-4"), format="json", **self.headers())
        matched_response = self.client.post(
            "/api/v1/finance/incoming-payments/",
            self._incoming_payment_payload(external_reference=f"BANK/{self.student.admission_number}/JAN", external_transaction_id="bank-recon-5"),
            format="json", **self.headers(),
        )
        self.assertEqual(matched_response.data["status"], "MATCHED")

        unmatched_only = self.client.get("/api/v1/finance/incoming-payments/?status=UNMATCHED", **self.headers())
        self.assertEqual(unmatched_only.data["count"], 1)

        future_only = self.client.get("/api/v1/finance/incoming-payments/?received_after=2999-01-01T00:00:00Z", **self.headers())
        self.assertEqual(future_only.data["count"], 0)

    def test_incoming_payment_malformed_id_and_missing_field(self):
        response = self.client.post(
            f"/api/v1/finance/incoming-payments/{uuid.uuid4()}/match/",
            {"student": str(self.student.id)}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 404)

        ingest_response = self.client.post(
            "/api/v1/finance/incoming-payments/",
            self._incoming_payment_payload(external_transaction_id="bank-recon-6"),
            format="json", **self.headers(),
        )
        response = self.client.post(
            f"/api/v1/finance/incoming-payments/{ingest_response.data['id']}/ignore/",
            {}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_incoming_payment_actions_require_their_own_permission(self):
        self.role.permissions = ["finance.reconciliation.view"]
        self.role.save(update_fields=["permissions"])

        response = self.client.post(
            "/api/v1/finance/incoming-payments/",
            self._incoming_payment_payload(external_transaction_id="bank-recon-7"),
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)
