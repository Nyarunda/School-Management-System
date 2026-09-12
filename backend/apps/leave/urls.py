from django.urls import path

from .api import (
    EmployeeLeaveAdjustmentView,
    EmployeeLeaveBalanceView,
    EmployeeLeaveCarryForwardView,
    EmployeeLeaveEntitlementView,
    LeaveRequestCancelView,
    LeaveRequestDecideView,
    LeaveRequestDetailView,
    LeaveRequestListCreateView,
    LeaveRequestSubmitView,
    LeaveRequestWithdrawView,
    LeaveSetupView,
    LeaveTypeDetailView,
    LeaveTypeListCreateView,
    LeaveWorkflowListCreateView,
    LeaveWorkflowStageDetailView,
    LeaveWorkflowStageListCreateView,
)

urlpatterns = [
    path("setup/", LeaveSetupView.as_view(), name="leave-setup"),
    path("types/", LeaveTypeListCreateView.as_view(), name="leave-type-list"),
    path("types/<uuid:leave_type_id>/", LeaveTypeDetailView.as_view(), name="leave-type-detail"),
    path("workflows/", LeaveWorkflowListCreateView.as_view(), name="leave-workflow-list"),
    path("workflows/<uuid:workflow_id>/stages/", LeaveWorkflowStageListCreateView.as_view(), name="leave-workflow-stage-list"),
    path("workflows/<uuid:workflow_id>/stages/<uuid:stage_id>/", LeaveWorkflowStageDetailView.as_view(), name="leave-workflow-stage-detail"),
    path("employees/<uuid:employee_id>/balance/", EmployeeLeaveBalanceView.as_view(), name="leave-employee-balance"),
    path("employees/<uuid:employee_id>/entitlement/", EmployeeLeaveEntitlementView.as_view(), name="leave-employee-entitlement"),
    path("employees/<uuid:employee_id>/carry-forward/", EmployeeLeaveCarryForwardView.as_view(), name="leave-employee-carry-forward"),
    path("employees/<uuid:employee_id>/adjustments/", EmployeeLeaveAdjustmentView.as_view(), name="leave-employee-adjustments"),
    path("requests/", LeaveRequestListCreateView.as_view(), name="leave-request-list"),
    path("requests/<uuid:request_id>/", LeaveRequestDetailView.as_view(), name="leave-request-detail"),
    path("requests/<uuid:request_id>/submit/", LeaveRequestSubmitView.as_view(), name="leave-request-submit"),
    path("requests/<uuid:request_id>/decide/", LeaveRequestDecideView.as_view(), name="leave-request-decide"),
    path("requests/<uuid:request_id>/withdraw/", LeaveRequestWithdrawView.as_view(), name="leave-request-withdraw"),
    path("requests/<uuid:request_id>/cancel/", LeaveRequestCancelView.as_view(), name="leave-request-cancel"),
]
