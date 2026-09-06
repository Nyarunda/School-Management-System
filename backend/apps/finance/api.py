from decimal import Decimal

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import AcademicLevel, AcademicYear
from apps.students.models import Student
from apps.tenancy.services import require_permission

from .models import (
    CreditNote,
    FeeCategory,
    FeeItem,
    FeeStructure,
    FeeStructureLine,
    FinanceSetup,
    Invoice,
    InvoiceLine,
    NumberSeries,
    StudentFeeAssignment,
    StudentLedgerEntry,
)
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


class FinancePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_finance_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        return require_permission(user=request.user, tenant_slug=slug, permission=permission).tenant
    except ValidationError as error:
        raise PermissionDenied(error.messages) from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


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
        category = get_object_or_404(FeeCategory.objects.for_tenant(tenant), pk=self.request.data.get("category"))
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
        except (KeyError, ValidationError) as error:
            return api_validation_error(error if isinstance(error, ValidationError) else ValidationError(str(error)))
        return Response(self.get_serializer(structure).data, status=status.HTTP_201_CREATED)


class FeeStructureLineCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, structure_id):
        tenant = resolve_finance_tenant(request, "finance.fee_structure.edit")
        structure = get_object_or_404(FeeStructure.objects.for_tenant(tenant), pk=structure_id)
        fee_item = get_object_or_404(FeeItem.objects.for_tenant(tenant), pk=request.data.get("fee_item"))
        try:
            line = add_fee_structure_line(
                user=request.user,
                tenant=tenant,
                fee_structure=structure,
                fee_item=fee_item,
                amount=Decimal(str(request.data["amount"])),
                is_required=request.data.get("is_required", True),
            )
        except (KeyError, ValidationError, ValueError) as error:
            return api_validation_error(error if isinstance(error, ValidationError) else ValidationError(str(error)))
        return Response(FeeStructureLineSerializer(line).data, status=status.HTTP_201_CREATED)


class FeeStructureApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, structure_id):
        tenant = resolve_finance_tenant(request, "finance.fee_structure.approve")
        structure = get_object_or_404(FeeStructure.objects.for_tenant(tenant), pk=structure_id)
        try:
            approve_fee_structure(user=request.user, tenant=tenant, fee_structure=structure)
        except ValidationError as error:
            return api_validation_error(error)
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
        student = get_object_or_404(Student.objects.for_tenant(tenant), pk=request.data.get("student"))
        structure = get_object_or_404(FeeStructure.objects.for_tenant(tenant), pk=request.data.get("fee_structure"))
        try:
            assignment = assign_fee_structure(user=request.user, tenant=tenant, student=student, fee_structure=structure)
        except ValidationError as error:
            return api_validation_error(error)
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
        return queryset.filter(student_id=student_id) if student_id else queryset


class InvoiceGenerateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, assignment_id):
        tenant = resolve_finance_tenant(request, "finance.invoice.create")
        assignment = get_object_or_404(StudentFeeAssignment.objects.for_tenant(tenant), pk=assignment_id)
        try:
            invoice = generate_invoice(user=request.user, tenant=tenant, assignment=assignment)
        except ValidationError as error:
            return api_validation_error(error)
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class InvoiceIssueView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, invoice_id):
        tenant = resolve_finance_tenant(request, "finance.invoice.issue")
        invoice = get_object_or_404(Invoice.objects.for_tenant(tenant), pk=invoice_id)
        try:
            invoice = issue_invoice(user=request.user, tenant=tenant, invoice=invoice)
        except ValidationError as error:
            return api_validation_error(error)
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
        student = get_object_or_404(Student.objects.for_tenant(tenant), pk=request.data.get("student"))
        invoice_id = request.data.get("invoice")
        invoice = get_object_or_404(Invoice.objects.for_tenant(tenant), pk=invoice_id) if invoice_id else None
        try:
            credit_note = issue_credit_note(
                user=request.user,
                tenant=tenant,
                student=student,
                invoice=invoice,
                amount=Decimal(str(request.data["amount"])),
                reason=request.data["reason"],
            )
        except (KeyError, ValidationError, ValueError) as error:
            return api_validation_error(error if isinstance(error, ValidationError) else ValidationError(str(error)))
        return Response(self.get_serializer(credit_note).data, status=status.HTTP_201_CREATED)


class StudentFinanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, student_id):
        tenant = resolve_finance_tenant(request, "finance.student_account.view")
        student = get_object_or_404(Student.objects.for_tenant(tenant), pk=student_id)
        invoices = Invoice.objects.for_tenant(tenant).filter(student=student).order_by("-created_at")
        ledger = StudentLedgerEntry.objects.for_tenant(tenant).filter(student=student).order_by("-posted_at")
        return Response({
            "student": {"id": student.id, "admission_number": student.admission_number, "name": student.full_name},
            "balance": student_balance(tenant=tenant, student=student),
            "invoices": InvoiceSerializer(invoices, many=True).data,
            "ledger": [{"id": entry.id, "entry_type": entry.entry_type, "amount": entry.amount, "posted_at": entry.posted_at} for entry in ledger],
        })