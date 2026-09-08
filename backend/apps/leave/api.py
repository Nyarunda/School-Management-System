from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListCreateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.platform.services import require_module_enabled
from apps.staff.models import Employee
from apps.tenancy.models import Role
from apps.tenancy.services import require_permission

from .models import (
    LeaveApprovalWorkflow,
    LeaveApprovalWorkflowStage,
    LeaveLedgerEntry,
    LeaveRequest,
    LeaveRequestApprovalStatus,
    LeaveSetup,
    LeaveType,
)
from .services import (
    add_workflow_stage,
    adjust_leave_balance,
    cancel_approved_leave_request,
    carry_forward_leave,
    configure_leave_setup,
    create_leave_request,
    create_leave_type,
    create_leave_workflow,
    decide_leave_request_stage,
    delete_workflow_stage,
    grant_leave_entitlement,
    resolve_leave_balance,
    submit_leave_request,
    update_leave_request,
    update_leave_type,
    update_workflow_stage,
    withdraw_leave_request,
)


class LeavePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_leave_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
        require_module_enabled(tenant=membership.tenant, module_code="staff_hr")
        return membership.tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_tenant_object(queryset, pk):
    try:
        return get_object_or_404(queryset, pk=pk)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


# --- Setup ---------------------------------------------------------------

class LeaveSetupSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveSetup
        fields = ["working_days", "leave_year_start_month", "leave_year_start_day"]


class LeaveSetupView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = resolve_leave_tenant(request, "leave.setup.view")
        setup = LeaveSetup.objects.filter(tenant=tenant).first()
        if setup is None:
            return Response({"working_days": [1, 2, 3, 4, 5], "leave_year_start_month": 1, "leave_year_start_day": 1})
        return Response(LeaveSetupSerializer(setup).data)

    def patch(self, request):
        tenant = resolve_leave_tenant(request, "leave.setup.manage")
        serializer = LeaveSetupSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            setup = configure_leave_setup(user=request.user, tenant=tenant, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveSetupSerializer(setup).data)


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = [
            "id", "name", "code", "default_annual_entitlement_days", "requires_balance", "allow_negative_balance",
            "allows_carry_forward", "max_carry_forward_days", "requires_approval", "approval_workflow", "is_active",
        ]
        read_only_fields = ["id"]


class LeaveTypeCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    code = serializers.CharField(max_length=30)
    default_annual_entitlement_days = serializers.IntegerField(min_value=0)
    requires_balance = serializers.BooleanField(required=False, default=True)
    allow_negative_balance = serializers.BooleanField(required=False, default=False)
    allows_carry_forward = serializers.BooleanField(required=False, default=False)
    max_carry_forward_days = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    requires_approval = serializers.BooleanField(required=False, default=True)
    approval_workflow = serializers.UUIDField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False, default=True)


class LeaveTypeUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    default_annual_entitlement_days = serializers.IntegerField(required=False, min_value=0)
    requires_balance = serializers.BooleanField(required=False)
    allow_negative_balance = serializers.BooleanField(required=False)
    allows_carry_forward = serializers.BooleanField(required=False)
    max_carry_forward_days = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    requires_approval = serializers.BooleanField(required=False)
    approval_workflow = serializers.UUIDField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)


class LeaveTypeListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LeaveTypeSerializer
    pagination_class = LeavePagination

    def get_queryset(self):
        return LeaveType.objects.filter(tenant=resolve_leave_tenant(self.request, "leave.setup.view")).order_by("code")

    def create(self, request, *args, **kwargs):
        tenant = resolve_leave_tenant(request, "leave.setup.manage")
        serializer = LeaveTypeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        workflow_id = data.pop("approval_workflow", None)
        workflow = resolve_tenant_object(LeaveApprovalWorkflow.objects.filter(tenant=tenant), str(workflow_id)) if workflow_id else None
        try:
            leave_type = create_leave_type(user=request.user, tenant=tenant, approval_workflow=workflow, **data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveTypeSerializer(leave_type).data, status=status.HTTP_201_CREATED)


class LeaveTypeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, leave_type_id):
        tenant = resolve_leave_tenant(request, "leave.setup.manage")
        leave_type = resolve_tenant_object(LeaveType.objects.filter(tenant=tenant), leave_type_id)
        serializer = LeaveTypeUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "approval_workflow" in data:
            workflow_id = data.pop("approval_workflow")
            data["approval_workflow"] = (
                resolve_tenant_object(LeaveApprovalWorkflow.objects.filter(tenant=tenant), str(workflow_id)) if workflow_id else None
            )
        try:
            updated = update_leave_type(user=request.user, tenant=tenant, leave_type=leave_type, **data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveTypeSerializer(updated).data)


class LeaveApprovalWorkflowSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveApprovalWorkflow
        fields = ["id", "name"]
        read_only_fields = ["id"]


class LeaveWorkflowListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LeaveApprovalWorkflowSerializer
    pagination_class = LeavePagination

    def get_queryset(self):
        return LeaveApprovalWorkflow.objects.filter(tenant=resolve_leave_tenant(self.request, "leave.setup.view")).order_by("name")

    def create(self, request, *args, **kwargs):
        tenant = resolve_leave_tenant(request, "leave.setup.manage")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            workflow = create_leave_workflow(user=request.user, tenant=tenant, name=serializer.validated_data["name"])
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveApprovalWorkflowSerializer(workflow).data, status=status.HTTP_201_CREATED)


class LeaveApprovalWorkflowStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveApprovalWorkflowStage
        fields = ["id", "sequence", "name", "approver_role"]
        read_only_fields = ["id"]


class LeaveWorkflowStageCreateSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(min_value=1)
    name = serializers.CharField(max_length=80)
    approver_role = serializers.IntegerField()


class LeaveWorkflowStageListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LeaveApprovalWorkflowStageSerializer
    pagination_class = LeavePagination

    def get_queryset(self):
        tenant = resolve_leave_tenant(self.request, "leave.setup.view")
        workflow = resolve_tenant_object(LeaveApprovalWorkflow.objects.filter(tenant=tenant), self.kwargs["workflow_id"])
        return LeaveApprovalWorkflowStage.objects.filter(workflow=workflow).order_by("sequence")

    def create(self, request, *args, **kwargs):
        tenant = resolve_leave_tenant(request, "leave.setup.manage")
        workflow = resolve_tenant_object(LeaveApprovalWorkflow.objects.filter(tenant=tenant), self.kwargs["workflow_id"])
        serializer = LeaveWorkflowStageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = resolve_tenant_object(Role.objects.filter(tenant=tenant), str(data["approver_role"]))
        try:
            stage = add_workflow_stage(
                user=request.user, tenant=tenant, workflow=workflow, sequence=data["sequence"], name=data["name"],
                approver_role=role,
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveApprovalWorkflowStageSerializer(stage).data, status=status.HTTP_201_CREATED)


class LeaveWorkflowStageUpdateSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(required=False, min_value=1)
    name = serializers.CharField(max_length=80, required=False)
    approver_role = serializers.IntegerField(required=False)


class LeaveWorkflowStageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, workflow_id, stage_id):
        tenant = resolve_leave_tenant(request, "leave.setup.manage")
        stage = resolve_tenant_object(LeaveApprovalWorkflowStage.objects.filter(tenant=tenant, workflow_id=workflow_id), stage_id)
        serializer = LeaveWorkflowStageUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "approver_role" in data:
            data["approver_role"] = resolve_tenant_object(Role.objects.filter(tenant=tenant), str(data["approver_role"]))
        try:
            updated = update_workflow_stage(user=request.user, tenant=tenant, stage=stage, **data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveApprovalWorkflowStageSerializer(updated).data)

    def delete(self, request, workflow_id, stage_id):
        tenant = resolve_leave_tenant(request, "leave.setup.manage")
        stage = resolve_tenant_object(LeaveApprovalWorkflowStage.objects.filter(tenant=tenant, workflow_id=workflow_id), stage_id)
        try:
            delete_workflow_stage(user=request.user, tenant=tenant, stage=stage)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Balance ---------------------------------------------------------------

class EmployeeLeaveBalanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, employee_id):
        tenant = resolve_leave_tenant(request, "leave.request.view")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        leave_type = resolve_tenant_object(LeaveType.objects.filter(tenant=tenant), request.query_params.get("leave_type"))
        try:
            year = int(request.query_params.get("year"))
        except (TypeError, ValueError):
            return Response({"detail": ["A valid year query parameter is required"]}, status=status.HTTP_400_BAD_REQUEST)

        balance = resolve_leave_balance(tenant=tenant, employee=employee, leave_type=leave_type, year=year)
        entries = LeaveLedgerEntry.objects.filter(
            tenant=tenant, employee=employee, leave_type=leave_type, leave_year=year,
        ).order_by("-created_at")
        entry_data = [
            {"id": str(e.id), "entry_type": e.entry_type, "days": e.days, "reason": e.reason, "created_at": e.created_at}
            for e in entries
        ]
        return Response({"balance": balance, "entries": entry_data})


class EntitlementGrantSerializer(serializers.Serializer):
    leave_type = serializers.UUIDField()
    year = serializers.IntegerField()
    days = serializers.IntegerField(required=False, allow_null=True)


class EmployeeLeaveEntitlementView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, employee_id):
        tenant = resolve_leave_tenant(request, "leave.balance.adjust")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        serializer = EntitlementGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        leave_type = resolve_tenant_object(LeaveType.objects.filter(tenant=tenant), str(data["leave_type"]))
        try:
            entry = grant_leave_entitlement(
                user=request.user, tenant=tenant, employee=employee, leave_type=leave_type, year=data["year"],
                days=data.get("days"),
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response({"id": str(entry.id), "days": entry.days}, status=status.HTTP_201_CREATED)


class CarryForwardSerializer(serializers.Serializer):
    leave_type = serializers.UUIDField()
    from_year = serializers.IntegerField()
    to_year = serializers.IntegerField()


class EmployeeLeaveCarryForwardView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, employee_id):
        tenant = resolve_leave_tenant(request, "leave.balance.adjust")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        serializer = CarryForwardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        leave_type = resolve_tenant_object(LeaveType.objects.filter(tenant=tenant), str(data["leave_type"]))
        try:
            entry = carry_forward_leave(
                user=request.user, tenant=tenant, employee=employee, leave_type=leave_type,
                from_year=data["from_year"], to_year=data["to_year"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response({"id": str(entry.id), "days": entry.days}, status=status.HTTP_201_CREATED)


class AdjustmentSerializer(serializers.Serializer):
    leave_type = serializers.UUIDField()
    year = serializers.IntegerField()
    days = serializers.IntegerField()
    reason = serializers.CharField(max_length=240)


class EmployeeLeaveAdjustmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, employee_id):
        tenant = resolve_leave_tenant(request, "leave.balance.adjust")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        serializer = AdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        leave_type = resolve_tenant_object(LeaveType.objects.filter(tenant=tenant), str(data["leave_type"]))
        try:
            entry = adjust_leave_balance(
                user=request.user, tenant=tenant, employee=employee, leave_type=leave_type, year=data["year"],
                days=data["days"], reason=data["reason"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response({"id": str(entry.id), "days": entry.days}, status=status.HTTP_201_CREATED)


# --- Requests ---------------------------------------------------------------

class LeaveRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveRequest
        fields = [
            "id", "employee", "leave_type", "start_date", "end_date", "requested_days", "reason", "status",
            "created_by", "created_at", "updated_at",
        ]
        read_only_fields = fields


class LeaveRequestCreateSerializer(serializers.Serializer):
    employee = serializers.UUIDField()
    leave_type = serializers.UUIDField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class LeaveRequestListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LeaveRequestSerializer
    pagination_class = LeavePagination

    def get_queryset(self):
        tenant = resolve_leave_tenant(self.request, "leave.request.view")
        queryset = LeaveRequest.objects.filter(tenant=tenant).order_by("-created_at")
        employee_id = self.request.query_params.get("employee")
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

    def create(self, request, *args, **kwargs):
        tenant = resolve_leave_tenant(request, "leave.request.manage")
        serializer = LeaveRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), str(data["employee"]))
        leave_type = resolve_tenant_object(LeaveType.objects.filter(tenant=tenant), str(data["leave_type"]))
        try:
            leave_request = create_leave_request(
                user=request.user, tenant=tenant, employee=employee, leave_type=leave_type,
                start_date=data["start_date"], end_date=data["end_date"], reason=data["reason"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveRequestSerializer(leave_request).data, status=status.HTTP_201_CREATED)


class LeaveRequestUpdateSerializer(serializers.Serializer):
    leave_type = serializers.UUIDField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    reason = serializers.CharField(max_length=240, required=False, allow_blank=True)


class LeaveRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, request_id):
        tenant = resolve_leave_tenant(request, "leave.request.view")
        leave_request = resolve_tenant_object(LeaveRequest.objects.filter(tenant=tenant), request_id)
        return Response(LeaveRequestSerializer(leave_request).data)

    def patch(self, request, request_id):
        tenant = resolve_leave_tenant(request, "leave.request.manage")
        leave_request = resolve_tenant_object(LeaveRequest.objects.filter(tenant=tenant), request_id)
        serializer = LeaveRequestUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "leave_type" in data:
            data["leave_type"] = resolve_tenant_object(LeaveType.objects.filter(tenant=tenant), str(data["leave_type"]))
        try:
            updated = update_leave_request(user=request.user, tenant=tenant, leave_request=leave_request, **data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveRequestSerializer(updated).data)


class LeaveRequestSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        tenant = resolve_leave_tenant(request, "leave.request.manage")
        leave_request = resolve_tenant_object(LeaveRequest.objects.filter(tenant=tenant), request_id)
        try:
            updated = submit_leave_request(user=request.user, tenant=tenant, leave_request=leave_request)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveRequestSerializer(updated).data)


class LeaveRequestDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=[LeaveRequestApprovalStatus.APPROVED, LeaveRequestApprovalStatus.REJECTED])
    comment = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class LeaveRequestDecideView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        tenant = resolve_leave_tenant(request, "leave.approve")
        leave_request = resolve_tenant_object(LeaveRequest.objects.filter(tenant=tenant), request_id)
        serializer = LeaveRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            updated = decide_leave_request_stage(
                user=request.user, tenant=tenant, leave_request=leave_request, decision=data["decision"],
                comment=data["comment"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveRequestSerializer(updated).data)


class LeaveRequestWithdrawView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        tenant = resolve_leave_tenant(request, "leave.request.manage")
        leave_request = resolve_tenant_object(LeaveRequest.objects.filter(tenant=tenant), request_id)
        try:
            updated = withdraw_leave_request(user=request.user, tenant=tenant, leave_request=leave_request)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveRequestSerializer(updated).data)


class LeaveRequestCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class LeaveRequestCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        tenant = resolve_leave_tenant(request, "leave.request.manage")
        leave_request = resolve_tenant_object(LeaveRequest.objects.filter(tenant=tenant), request_id)
        serializer = LeaveRequestCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = cancel_approved_leave_request(
                user=request.user, tenant=tenant, leave_request=leave_request, reason=serializer.validated_data["reason"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(LeaveRequestSerializer(updated).data)
