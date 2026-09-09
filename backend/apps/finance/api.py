from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView, RetrieveAPIView, RetrieveUpdateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import AcademicLevel, AcademicYear
from apps.activity.services import record_activity
from apps.students.models import Student
from apps.platform.services import require_module_enabled
from apps.tenancy.services import require_permission

from .models import (
    AllocationReversal,
    CreditNote,
    CreditNoteStatus,
    FeeCategory,
    FeeItem,
    FeeStructure,
    FeeStructureLine,
    FinanceSetup,
    IncomingPayment,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    NumberSeries,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentReversal,
    PaymentStatus,
    Receipt,
    StudentFeeAssignment,
    StudentLedgerEntry,
)
from .selectors import student_balance
from .services import (
    add_fee_structure_line,
    allocate_payment,
    approve_fee_structure,
    assign_fee_structure,
    create_fee_structure,
    generate_invoice,
    ignore_incoming_payment,
    ingest_incoming_payment,
    issue_credit_note,
    issue_invoice,
    match_incoming_payment,
    record_payment,
    reverse_allocation,
    reverse_payment,
)


class FinancePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_finance_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
        require_module_enabled(tenant=membership.tenant, module_code="finance")
        return membership.tenant
    except ValidationError as error:
        raise PermissionDenied(error.messages) from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


def resolve_tenant_object(queryset, pk):
    """Fetch a tenant-scoped object by primary key.

    A malformed identifier (e.g. a non-UUID string) makes Django's ORM raise
    a bare ValidationError while resolving the lookup, which DRF's default
    exception handler does not translate into a response. Treat a malformed
    identifier the same as a missing one (404) instead of letting it surface
    as an unhandled 500.
    """
    try:
        return get_object_or_404(queryset, pk=pk)
    except ValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


class FinanceSetupSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinanceSetup
        fields = ["id", "currency", "fiscal_year_start_month", "configuration"]
        read_only_fields = ["id"]


class FinanceSetupView(RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = FinanceSetupSerializer

    def get_object(self):
        tenant = resolve_finance_tenant(self.request, "finance.setup.view")
        return FinanceSetup.objects.for_tenant(tenant).first()

    def update(self, request, *args, **kwargs):
        tenant = resolve_finance_tenant(request, "finance.setup.manage")
        setup, _ = FinanceSetup.objects.get_or_create(tenant=tenant)
        serializer = self.get_serializer(setup, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # Bounded metadata only -- never the raw configuration payload, which
        # is arbitrary tenant-supplied JSON and may itself carry sensitive data.
        record_activity(
            tenant=tenant,
            actor=request.user,
            action="finance_setup.updated",
            resource_type="finance_setup",
            resource_id=str(setup.id),
            metadata={"changed_fields": sorted(request.data.keys())},
        )
        return Response(serializer.data)


class FeeCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeCategory
        fields = ["id", "name", "code", "is_active"]
        read_only_fields = ["id"]


class FeeCategoryListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = FeeCategorySerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        return FeeCategory.objects.for_tenant(resolve_finance_tenant(self.request, "finance.setup.view")).order_by("name")

    def perform_create(self, serializer):
        tenant = resolve_finance_tenant(self.request, "finance.setup.manage")
        serializer.save(tenant=tenant)


class FeeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeItem
        fields = ["id", "category", "name", "code", "is_optional", "is_active"]
        read_only_fields = ["id"]


class FeeItemListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = FeeItemSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        return FeeItem.objects.for_tenant(resolve_finance_tenant(self.request, "finance.setup.view")).select_related("category").order_by("name")

    def perform_create(self, serializer):
        tenant = resolve_finance_tenant(self.request, "finance.setup.manage")
        category = resolve_tenant_object(FeeCategory.objects.for_tenant(tenant), self.request.data.get("category"))
        serializer.save(tenant=tenant, category=category)


class FeeStructureLineSerializer(serializers.ModelSerializer):
    fee_item_name = serializers.CharField(source="fee_item.name", read_only=True)

    class Meta:
        model = FeeStructureLine
        fields = ["id", "fee_item", "fee_item_name", "amount", "is_required"]
        read_only_fields = ["id", "fee_item_name"]


class FeeStructureSerializer(serializers.ModelSerializer):
    lines = FeeStructureLineSerializer(many=True, read_only=True)

    class Meta:
        model = FeeStructure
        fields = ["id", "name", "academic_year", "academic_level", "is_active", "is_approved", "lines"]
        read_only_fields = ["id", "is_approved", "lines"]


class FeeStructureListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = FeeStructureSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.fee_structure.view")
        return FeeStructure.objects.for_tenant(tenant).select_related("academic_year", "academic_level").prefetch_related("lines__fee_item").order_by("name")

    def create(self, request, *args, **kwargs):
        tenant = resolve_finance_tenant(request, "finance.fee_structure.create")
        try:
            academic_year = get_object_or_404(AcademicYear.objects.for_tenant(tenant), pk=request.data.get("academic_year"))
            academic_level = get_object_or_404(AcademicLevel.objects.for_tenant(tenant), pk=request.data.get("academic_level"))
            structure = create_fee_structure(
                user=request.user,
                tenant=tenant,
                name=request.data["name"],
                academic_year=academic_year,
                academic_level=academic_level,
            )
        except (KeyError, ValidationError, IntegrityError) as error:
            return api_validation_error(error if isinstance(error, ValidationError) else ValidationError(str(error)))
        return Response(self.get_serializer(structure).data, status=status.HTTP_201_CREATED)


class FeeStructureLineCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, structure_id):
        tenant = resolve_finance_tenant(request, "finance.fee_structure.edit")
        structure = get_object_or_404(FeeStructure.objects.for_tenant(tenant), pk=structure_id)
        fee_item = resolve_tenant_object(FeeItem.objects.for_tenant(tenant), request.data.get("fee_item"))
        try:
            line = add_fee_structure_line(
                user=request.user,
                tenant=tenant,
                fee_structure=structure,
                fee_item=fee_item,
                amount=Decimal(str(request.data["amount"])),
                is_required=request.data.get("is_required", True),
            )
        except (KeyError, ValidationError, ValueError, InvalidOperation, IntegrityError) as error:
            return api_validation_error(error if isinstance(error, ValidationError) else ValidationError(str(error)))
        return Response(FeeStructureLineSerializer(line).data, status=status.HTTP_201_CREATED)


class FeeStructureApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, structure_id):
        tenant = resolve_finance_tenant(request, "finance.fee_structure.approve")
        structure = get_object_or_404(FeeStructure.objects.for_tenant(tenant), pk=structure_id)
        approve_fee_structure(user=request.user, tenant=tenant, fee_structure=structure)
        return Response(FeeStructureSerializer(structure).data)


class AssignmentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    fee_structure_name = serializers.CharField(source="fee_structure.name", read_only=True)

    class Meta:
        model = StudentFeeAssignment
        fields = ["id", "student", "student_name", "fee_structure", "fee_structure_name", "status", "assigned_at"]
        read_only_fields = ["id", "status", "assigned_at", "student_name", "fee_structure_name"]


class AssignmentListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AssignmentSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.student_account.view")
        return StudentFeeAssignment.objects.for_tenant(tenant).select_related("student", "fee_structure").order_by("-assigned_at")

    def create(self, request, *args, **kwargs):
        tenant = resolve_finance_tenant(request, "finance.invoice.create")
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), request.data.get("student"))
        structure = resolve_tenant_object(FeeStructure.objects.for_tenant(tenant), request.data.get("fee_structure"))
        assignment = assign_fee_structure(user=request.user, tenant=tenant, student=student, fee_structure=structure)
        return Response(self.get_serializer(assignment).data, status=status.HTTP_201_CREATED)


class InvoiceSerializer(serializers.ModelSerializer):
    lines = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = ["id", "invoice_number", "student", "assignment", "status", "subtotal", "discount_total", "total", "issued_at", "created_at", "lines"]
        read_only_fields = fields

    def get_lines(self, invoice):
        return InvoiceLineSerializer(invoice.lines.all(), many=True).data


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ["id", "fee_item", "description", "quantity", "unit_amount", "discount_amount", "net_amount"]


class InvoiceListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = InvoiceSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.invoice.view")
        queryset = Invoice.objects.for_tenant(tenant).select_related("student", "assignment").prefetch_related("lines").order_by("-created_at")
        student_id = self.request.query_params.get("student")
        if not student_id:
            return queryset
        try:
            return queryset.filter(student_id=student_id)
        except ValidationError as error:
            raise NotFound("No matching record for the given identifier") from error


class InvoiceGenerateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, assignment_id):
        tenant = resolve_finance_tenant(request, "finance.invoice.create")
        assignment = get_object_or_404(StudentFeeAssignment.objects.for_tenant(tenant), pk=assignment_id)
        invoice = generate_invoice(user=request.user, tenant=tenant, assignment=assignment)
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class InvoiceIssueView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, invoice_id):
        tenant = resolve_finance_tenant(request, "finance.invoice.issue")
        invoice = get_object_or_404(Invoice.objects.for_tenant(tenant), pk=invoice_id)
        invoice = issue_invoice(user=request.user, tenant=tenant, invoice=invoice)
        return Response(InvoiceSerializer(invoice).data)


class CreditNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CreditNote
        fields = ["id", "credit_note_number", "student", "invoice", "reason", "amount", "status", "issued_at"]
        read_only_fields = ["id", "credit_note_number", "status", "issued_at"]


class CreditNoteListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CreditNoteSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.student_account.view")
        return CreditNote.objects.for_tenant(tenant).order_by("-issued_at")

    def create(self, request, *args, **kwargs):
        tenant = resolve_finance_tenant(request, "finance.credit_note.create")
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), request.data.get("student"))
        invoice_id = request.data.get("invoice")
        invoice = resolve_tenant_object(Invoice.objects.for_tenant(tenant), invoice_id) if invoice_id else None
        try:
            credit_note = issue_credit_note(
                user=request.user,
                tenant=tenant,
                student=student,
                invoice=invoice,
                amount=Decimal(str(request.data["amount"])),
                reason=request.data["reason"],
            )
        except (KeyError, ValidationError, ValueError, InvalidOperation) as error:
            return api_validation_error(error if isinstance(error, ValidationError) else ValidationError(str(error)))
        return Response(self.get_serializer(credit_note).data, status=status.HTTP_201_CREATED)


class ReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Receipt
        fields = ["id", "receipt_number", "issued_at"]
        read_only_fields = fields


class PaymentAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAllocation
        fields = ["id", "payment", "invoice", "amount", "allocated_at"]
        read_only_fields = fields


class AllocationReversalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AllocationReversal
        fields = ["id", "allocation", "amount", "reason", "reversed_at"]
        read_only_fields = fields


# Money amounts use a plain serializers.Serializer with an explicit DecimalField
# (max_digits/decimal_places matching the model, min_value rejecting zero/negative)
# rather than Decimal(str(request.data[...])). DRF's DecimalField already rejects
# non-finite input (NaN, Infinity, -Infinity) and wrong precision/scale at the
# request boundary, before any domain/service code ever sees it.
class PaymentCreateSerializer(serializers.Serializer):
    student = serializers.UUIDField()
    payment_method = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    idempotency_key = serializers.CharField(max_length=120)
    external_reference = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")


class PaymentAllocationCreateSerializer(serializers.Serializer):
    invoice = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))


class AllocationReversalCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    reason = serializers.CharField(max_length=240)


class PaymentReversalSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentReversal
        fields = ["id", "reversal_number", "reason", "reversed_at"]
        read_only_fields = fields


class PaymentReversalCreateSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=240)


class PaymentSerializer(serializers.ModelSerializer):
    receipt = ReceiptSerializer(read_only=True)
    allocations = PaymentAllocationSerializer(many=True, read_only=True)
    reversal = PaymentReversalSerializer(read_only=True)
    allocated_amount = serializers.SerializerMethodField()
    unallocated_amount = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id", "student", "payment_method", "amount", "external_reference",
            "idempotency_key", "status", "received_at", "created_at",
            "receipt", "allocations", "reversal", "allocated_amount", "unallocated_amount",
        ]
        read_only_fields = ["id", "status", "received_at", "created_at", "receipt", "allocations", "reversal", "allocated_amount", "unallocated_amount"]

    def get_allocated_amount(self, payment):
        # Net of reversals: a reversed allocation frees that cash again.
        # Relies on "allocations__reversals" being prefetched by the caller.
        total = Decimal("0")
        for allocation in payment.allocations.all():
            total += allocation.amount - sum((reversal.amount for reversal in allocation.reversals.all()), Decimal("0"))
        return total

    def get_unallocated_amount(self, payment):
        if payment.status == PaymentStatus.REVERSED:
            return Decimal("0")
        return payment.amount - self.get_allocated_amount(payment)


class PaymentListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PaymentSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.payment.view")
        queryset = (
            Payment.objects.for_tenant(tenant)
            .select_related("student", "payment_method")
            .prefetch_related("allocations__reversals", "receipt", "reversal")
            .order_by("-received_at")
        )
        student_id = self.request.query_params.get("student")
        if not student_id:
            return queryset
        try:
            return queryset.filter(student_id=student_id)
        except ValidationError as error:
            raise NotFound("No matching record for the given identifier") from error

    def create(self, request, *args, **kwargs):
        tenant = resolve_finance_tenant(request, "finance.payment.record")
        input_serializer = PaymentCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), str(data["student"]))
        payment_method = resolve_tenant_object(PaymentMethod.objects.for_tenant(tenant), str(data["payment_method"]))
        payment = record_payment(
            user=request.user,
            tenant=tenant,
            student=student,
            payment_method=payment_method,
            amount=data["amount"],
            idempotency_key=data["idempotency_key"],
            external_reference=data["external_reference"],
        )
        return Response(self.get_serializer(payment).data, status=status.HTTP_201_CREATED)


class PaymentDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PaymentSerializer

    def get_object(self):
        tenant = resolve_finance_tenant(self.request, "finance.payment.view")
        queryset = (
            Payment.objects.for_tenant(tenant)
            .select_related("student", "payment_method")
            .prefetch_related("allocations__reversals", "receipt", "reversal")
        )
        return resolve_tenant_object(queryset, self.kwargs["payment_id"])


class PaymentReversalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, payment_id):
        tenant = resolve_finance_tenant(request, "finance.payment.reverse")
        payment = resolve_tenant_object(Payment.objects.for_tenant(tenant), payment_id)
        input_serializer = PaymentReversalCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reversal = reverse_payment(user=request.user, tenant=tenant, payment=payment, reason=input_serializer.validated_data["reason"])
        return Response(PaymentReversalSerializer(reversal).data, status=status.HTTP_201_CREATED)


class PaymentAllocateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, payment_id):
        tenant = resolve_finance_tenant(request, "finance.payment.allocate")
        payment = resolve_tenant_object(Payment.objects.for_tenant(tenant), payment_id)
        input_serializer = PaymentAllocationCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data
        invoice = resolve_tenant_object(Invoice.objects.for_tenant(tenant), str(data["invoice"]))
        allocation = allocate_payment(user=request.user, tenant=tenant, payment=payment, invoice=invoice, amount=data["amount"])
        return Response(PaymentAllocationSerializer(allocation).data, status=status.HTTP_201_CREATED)


class AllocationReversalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, allocation_id):
        tenant = resolve_finance_tenant(request, "finance.allocation.reverse")
        allocation = resolve_tenant_object(PaymentAllocation.objects.for_tenant(tenant), allocation_id)
        input_serializer = AllocationReversalCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data
        reversal = reverse_allocation(user=request.user, tenant=tenant, allocation=allocation, amount=data["amount"], reason=data["reason"])
        return Response(AllocationReversalSerializer(reversal).data, status=status.HTTP_201_CREATED)


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentLedgerEntry
        fields = ["id", "entry_type", "amount", "posted_at", "invoice", "credit_note", "payment_allocation", "allocation_reversal"]
        read_only_fields = fields


class StudentLedgerListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LedgerEntrySerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.student_account.view")
        queryset = StudentLedgerEntry.objects.for_tenant(tenant).order_by("-posted_at")
        student_id = self.request.query_params.get("student")
        if not student_id:
            return queryset
        try:
            return queryset.filter(student_id=student_id)
        except ValidationError as error:
            raise NotFound("No matching record for the given identifier") from error


class StudentFinanceView(APIView):
    """A bounded snapshot for a student's account -- summary totals plus a
    short recent-activity preview. Full history is independently paginated
    through /invoices/, /payments/, and /ledger-entries/ with ?student=,
    not returned here in full (see docs/architecture/api-query-performance.md).
    """

    permission_classes = [IsAuthenticated]
    RECENT_LIMIT = 5

    def get(self, request, student_id):
        tenant = resolve_finance_tenant(request, "finance.student_account.view")
        student = get_object_or_404(Student.objects.for_tenant(tenant), pk=student_id)

        invoices = Invoice.objects.for_tenant(tenant).filter(student=student)
        total_invoiced = invoices.filter(status=InvoiceStatus.ISSUED).aggregate(total=Sum("total"))["total"] or Decimal("0")

        credit_notes = CreditNote.objects.for_tenant(tenant).filter(student=student, status=CreditNoteStatus.ISSUED)
        total_credited = credit_notes.aggregate(total=Sum("amount"))["total"] or Decimal("0")

        payments = Payment.objects.for_tenant(tenant).filter(student=student)
        total_received = payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        allocated = PaymentAllocation.objects.filter(tenant=tenant, payment__student=student).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        reversed_amount = AllocationReversal.objects.filter(tenant=tenant, allocation__payment__student=student).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        total_paid = allocated - reversed_amount

        recent_invoices = invoices.prefetch_related("lines").order_by("-created_at")[: self.RECENT_LIMIT]
        recent_payments = (
            payments.select_related("payment_method")
            .prefetch_related("allocations__reversals", "receipt", "reversal")
            .order_by("-received_at")[: self.RECENT_LIMIT]
        )
        recent_ledger_entries = StudentLedgerEntry.objects.for_tenant(tenant).filter(student=student).order_by("-posted_at")[: self.RECENT_LIMIT]

        return Response({
            "student": {"id": student.id, "admission_number": student.admission_number, "name": student.full_name},
            "summary": {
                "outstanding_balance": student_balance(tenant=tenant, student=student),
                "total_invoiced": total_invoiced,
                "total_credited": total_credited,
                "total_paid": total_paid,
                "unapplied_cash": total_received - total_paid,
            },
            "recent_invoices": InvoiceSerializer(recent_invoices, many=True).data,
            "recent_payments": PaymentSerializer(recent_payments, many=True).data,
            "recent_ledger_entries": LedgerEntrySerializer(recent_ledger_entries, many=True).data,
        })


class IncomingPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncomingPayment
        fields = [
            "id", "payment_method", "amount", "external_reference", "external_transaction_id",
            "status", "matched_payment", "ignored_reason", "received_at", "created_at",
        ]
        read_only_fields = ["id", "status", "matched_payment", "ignored_reason", "created_at"]


class IncomingPaymentCreateSerializer(serializers.Serializer):
    payment_method = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    external_reference = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")
    external_transaction_id = serializers.CharField(max_length=120)


class IncomingPaymentMatchSerializer(serializers.Serializer):
    student = serializers.UUIDField()


class IncomingPaymentIgnoreSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=240)


class IncomingPaymentListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = IncomingPaymentSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.reconciliation.view")
        queryset = IncomingPayment.objects.for_tenant(tenant).select_related("payment_method").order_by("-received_at")
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        received_after = self.request.query_params.get("received_after")
        if received_after:
            queryset = queryset.filter(received_at__gte=received_after)
        return queryset

    def create(self, request, *args, **kwargs):
        tenant = resolve_finance_tenant(request, "finance.reconciliation.ingest")
        input_serializer = IncomingPaymentCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data
        payment_method = resolve_tenant_object(PaymentMethod.objects.for_tenant(tenant), str(data["payment_method"]))
        incoming = ingest_incoming_payment(
            user=request.user,
            tenant=tenant,
            payment_method=payment_method,
            amount=data["amount"],
            external_reference=data["external_reference"],
            external_transaction_id=data["external_transaction_id"],
        )
        return Response(self.get_serializer(incoming).data, status=status.HTTP_201_CREATED)


class IncomingPaymentMatchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, incoming_payment_id):
        tenant = resolve_finance_tenant(request, "finance.reconciliation.match")
        incoming = resolve_tenant_object(IncomingPayment.objects.for_tenant(tenant), incoming_payment_id)
        input_serializer = IncomingPaymentMatchSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), str(input_serializer.validated_data["student"]))
        matched = match_incoming_payment(user=request.user, tenant=tenant, incoming=incoming, student=student)
        return Response(IncomingPaymentSerializer(matched).data)


class IncomingPaymentIgnoreView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, incoming_payment_id):
        tenant = resolve_finance_tenant(request, "finance.reconciliation.ignore")
        incoming = resolve_tenant_object(IncomingPayment.objects.for_tenant(tenant), incoming_payment_id)
        input_serializer = IncomingPaymentIgnoreSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        ignored = ignore_incoming_payment(user=request.user, tenant=tenant, incoming=incoming, reason=input_serializer.validated_data["reason"])
        return Response(IncomingPaymentSerializer(ignored).data)