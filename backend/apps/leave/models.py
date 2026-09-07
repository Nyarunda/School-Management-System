import uuid

from django.db import models

from apps.staff.models import Employee
from apps.tenancy.models import Role, TenantOwnedModel, User


def default_working_days():
    return [1, 2, 3, 4, 5]  # ISO weekday: Mon=1..Sun=7


class LeaveSetup(TenantOwnedModel):
    """One per tenant. Deliberately separate from AttendanceSetup.instructional_days
    (apps/attendance/models.py) -- staff working days and student instructional
    days are different concepts that happen to default to the same five weekdays.
    """
    working_days = models.JSONField(default=default_working_days)
    leave_year_start_month = models.PositiveSmallIntegerField(default=1)
    leave_year_start_day = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="unique_leave_setup_per_tenant")]


class LeaveApprovalWorkflow(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="unique_leave_workflow_name_per_tenant")]


class LeaveApprovalWorkflowStage(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(LeaveApprovalWorkflow, on_delete=models.CASCADE, related_name="stages")
    sequence = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=80)
    approver_role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workflow", "sequence"], name="unique_stage_sequence_per_workflow")]


class LeaveType(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30)
    default_annual_entitlement_days = models.PositiveSmallIntegerField()
    requires_balance = models.BooleanField(default=True)
    allow_negative_balance = models.BooleanField(default=False)
    allows_carry_forward = models.BooleanField(default=False)
    max_carry_forward_days = models.PositiveSmallIntegerField(null=True, blank=True)
    requires_approval = models.BooleanField(default=True)
    approval_workflow = models.ForeignKey(
        LeaveApprovalWorkflow, on_delete=models.PROTECT, null=True, blank=True, related_name="leave_types",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_leave_type_code_per_tenant")]


class LeaveRequestStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


class LeaveRequest(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="leave_requests")
    start_date = models.DateField()
    end_date = models.DateField()
    requested_days = models.PositiveSmallIntegerField()
    reason = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=20, choices=LeaveRequestStatus.choices, default=LeaveRequestStatus.DRAFT)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "employee", "status"])]


class LeaveRequestApprovalStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    SKIPPED = "SKIPPED", "Skipped"


class LeaveRequestApproval(TenantOwnedModel):
    """Snapshotted from the LeaveType's LeaveApprovalWorkflow at submission
    time -- same discipline as Attendance's NOT_MARKED roster and Assessments'
    AssessmentGradingBand: a later edit to a workflow's stages never
    retroactively changes an in-flight request's required stages."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name="approvals")
    sequence = models.PositiveSmallIntegerField()
    stage_name = models.CharField(max_length=80)
    approver_role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(
        max_length=20, choices=LeaveRequestApprovalStatus.choices, default=LeaveRequestApprovalStatus.PENDING,
    )
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    comment = models.CharField(max_length=240, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["leave_request", "sequence"], name="unique_stage_sequence_per_request")]


class LeaveLedgerEntryType(models.TextChoices):
    ENTITLEMENT = "ENTITLEMENT", "Entitlement"
    CARRY_FORWARD = "CARRY_FORWARD", "Carry forward"
    CONSUMED = "CONSUMED", "Consumed"
    REVERSAL = "REVERSAL", "Reversal"
    ADJUSTMENT = "ADJUSTMENT", "Adjustment"


class LeaveLedgerEntry(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_ledger_entries")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="ledger_entries")
    leave_year = models.PositiveSmallIntegerField()
    entry_type = models.CharField(max_length=20, choices=LeaveLedgerEntryType.choices)
    days = models.SmallIntegerField()
    leave_request = models.ForeignKey(
        LeaveRequest, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries",
    )
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "employee", "leave_type", "leave_year"])]
