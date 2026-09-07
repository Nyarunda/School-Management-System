from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.activity.services import record_activity
from apps.staff.models import Employee
from apps.tenancy.services import require_permission, require_same_tenant

from .models import (
    LeaveApprovalWorkflow,
    LeaveApprovalWorkflowStage,
    LeaveLedgerEntry,
    LeaveLedgerEntryType,
    LeaveRequest,
    LeaveRequestApproval,
    LeaveRequestApprovalStatus,
    LeaveRequestStatus,
    LeaveSetup,
    LeaveType,
    default_working_days,
)


def _require_campus_scope(*, membership, campus_id):
    if membership.campus_id is not None and campus_id != membership.campus_id:
        raise ValidationError("User is not authorized for this campus")


def _require_tracks_balance(leave_type):
    if not leave_type.requires_balance:
        raise ValidationError("This leave type does not track a balance")


def resolve_leave_year(*, tenant, for_date):
    setup = LeaveSetup.objects.filter(tenant=tenant).first()
    month = setup.leave_year_start_month if setup else 1
    day = setup.leave_year_start_day if setup else 1
    anchor = date(for_date.year, month, day)
    return for_date.year if for_date >= anchor else for_date.year - 1


# --- Setup ---------------------------------------------------------------

def configure_leave_setup(*, user, tenant, working_days=None, leave_year_start_month=None, leave_year_start_day=None):
    require_permission(user=user, tenant=tenant, permission="leave.setup.manage")
    if leave_year_start_month is not None and not (1 <= leave_year_start_month <= 12):
        raise ValidationError("leave_year_start_month must be between 1 and 12")
    if leave_year_start_day is not None and not (1 <= leave_year_start_day <= 28):
        raise ValidationError("leave_year_start_day must be between 1 and 28")

    setup, _ = LeaveSetup.objects.get_or_create(tenant=tenant)
    changed_fields = []
    if working_days is not None:
        setup.working_days = working_days
        changed_fields.append("working_days")
    if leave_year_start_month is not None:
        setup.leave_year_start_month = leave_year_start_month
        changed_fields.append("leave_year_start_month")
    if leave_year_start_day is not None:
        setup.leave_year_start_day = leave_year_start_day
        changed_fields.append("leave_year_start_day")
    if changed_fields:
        setup.save(update_fields=changed_fields)
    return setup


def _validate_carry_forward_fields(allows_carry_forward, max_carry_forward_days):
    if allows_carry_forward and max_carry_forward_days is None:
        raise ValidationError("max_carry_forward_days is required when allows_carry_forward is enabled")
    if not allows_carry_forward and max_carry_forward_days is not None:
        raise ValidationError("max_carry_forward_days must be empty when allows_carry_forward is disabled")


def create_leave_type(
    *, user, tenant, name, code, default_annual_entitlement_days, requires_balance=True,
    allow_negative_balance=False, allows_carry_forward=False, max_carry_forward_days=None,
    requires_approval=True, approval_workflow=None, is_active=True,
):
    require_permission(user=user, tenant=tenant, permission="leave.setup.manage")
    if approval_workflow is not None:
        require_same_tenant(tenant=tenant, approval_workflow=approval_workflow)
    _validate_carry_forward_fields(allows_carry_forward, max_carry_forward_days)

    return LeaveType.objects.create(
        tenant=tenant, name=name, code=code, default_annual_entitlement_days=default_annual_entitlement_days,
        requires_balance=requires_balance, allow_negative_balance=allow_negative_balance,
        allows_carry_forward=allows_carry_forward, max_carry_forward_days=max_carry_forward_days,
        requires_approval=requires_approval, approval_workflow=approval_workflow, is_active=is_active,
    )


LEAVE_TYPE_UPDATABLE_FIELDS = [
    "name", "default_annual_entitlement_days", "requires_balance", "allow_negative_balance",
    "allows_carry_forward", "max_carry_forward_days", "requires_approval", "approval_workflow", "is_active",
]


def update_leave_type(*, user, tenant, leave_type, **fields):
    require_permission(user=user, tenant=tenant, permission="leave.setup.manage")
    require_same_tenant(tenant=tenant, leave_type=leave_type)

    unknown = set(fields) - set(LEAVE_TYPE_UPDATABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Unsupported field(s): {', '.join(sorted(unknown))}")

    approval_workflow = fields.get("approval_workflow")
    if "approval_workflow" in fields and approval_workflow is not None:
        require_same_tenant(tenant=tenant, approval_workflow=approval_workflow)

    effective_allows_carry_forward = fields.get("allows_carry_forward", leave_type.allows_carry_forward)
    effective_max_carry_forward_days = fields.get("max_carry_forward_days", leave_type.max_carry_forward_days)
    _validate_carry_forward_fields(effective_allows_carry_forward, effective_max_carry_forward_days)

    for field, value in fields.items():
        setattr(leave_type, field, value)
    leave_type.save(update_fields=list(fields.keys()))
    return leave_type


def create_leave_workflow(*, user, tenant, name):
    require_permission(user=user, tenant=tenant, permission="leave.setup.manage")
    return LeaveApprovalWorkflow.objects.create(tenant=tenant, name=name)


def add_workflow_stage(*, user, tenant, workflow, sequence, name, approver_role):
    require_permission(user=user, tenant=tenant, permission="leave.setup.manage")
    require_same_tenant(tenant=tenant, workflow=workflow, approver_role=approver_role)
    return LeaveApprovalWorkflowStage.objects.create(
        tenant=tenant, workflow=workflow, sequence=sequence, name=name, approver_role=approver_role,
    )


def update_workflow_stage(*, user, tenant, stage, **fields):
    require_permission(user=user, tenant=tenant, permission="leave.setup.manage")
    require_same_tenant(tenant=tenant, stage=stage)

    approver_role = fields.get("approver_role")
    if "approver_role" in fields and approver_role is not None:
        require_same_tenant(tenant=tenant, approver_role=approver_role)

    for field, value in fields.items():
        setattr(stage, field, value)
    stage.save(update_fields=list(fields.keys()))
    return stage


def delete_workflow_stage(*, user, tenant, stage):
    require_permission(user=user, tenant=tenant, permission="leave.setup.manage")
    require_same_tenant(tenant=tenant, stage=stage)
    stage.delete()


# --- Balance --------------------------------------------------------------

def resolve_leave_balance(*, tenant, employee, leave_type, year):
    total = LeaveLedgerEntry.objects.filter(
        tenant=tenant, employee=employee, leave_type=leave_type, leave_year=year,
    ).aggregate(total=Sum("days"))["total"]
    return total or 0


def grant_leave_entitlement(*, user, tenant, employee, leave_type, year, days=None, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="leave.balance.adjust")
    require_same_tenant(tenant=tenant, employee=employee, leave_type=leave_type)
    _require_campus_scope(membership=membership, campus_id=employee.campus_id)
    _require_tracks_balance(leave_type)

    days = days if days is not None else leave_type.default_annual_entitlement_days
    entry = LeaveLedgerEntry.objects.create(
        tenant=tenant, employee=employee, leave_type=leave_type, leave_year=year,
        entry_type=LeaveLedgerEntryType.ENTITLEMENT, days=days, recorded_by=actor or user,
    )
    record_activity(
        tenant=tenant, actor=actor or user, action="leave.entitlement_granted",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"leave_type_id": str(leave_type.id), "year": year, "days": days},
    )
    return entry


def carry_forward_leave(*, user, tenant, employee, leave_type, from_year, to_year, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="leave.balance.adjust")
    require_same_tenant(tenant=tenant, employee=employee, leave_type=leave_type)
    _require_campus_scope(membership=membership, campus_id=employee.campus_id)
    _require_tracks_balance(leave_type)
    if not leave_type.allows_carry_forward:
        raise ValidationError("This leave type does not allow carry-forward")

    balance = resolve_leave_balance(tenant=tenant, employee=employee, leave_type=leave_type, year=from_year)
    if balance <= 0:
        raise ValidationError("No positive balance available to carry forward")

    days = balance
    if leave_type.max_carry_forward_days is not None:
        days = min(days, leave_type.max_carry_forward_days)

    entry = LeaveLedgerEntry.objects.create(
        tenant=tenant, employee=employee, leave_type=leave_type, leave_year=to_year,
        entry_type=LeaveLedgerEntryType.CARRY_FORWARD, days=days, recorded_by=actor or user,
    )
    record_activity(
        tenant=tenant, actor=actor or user, action="leave.carried_forward",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"leave_type_id": str(leave_type.id), "from_year": from_year, "to_year": to_year, "days": days},
    )
    return entry


def adjust_leave_balance(*, user, tenant, employee, leave_type, year, days, reason, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="leave.balance.adjust")
    require_same_tenant(tenant=tenant, employee=employee, leave_type=leave_type)
    _require_campus_scope(membership=membership, campus_id=employee.campus_id)
    _require_tracks_balance(leave_type)
    if not reason:
        raise ValidationError("A reason is required for a manual balance adjustment")
    if days == 0:
        raise ValidationError("Adjustment days must be non-zero")

    entry = LeaveLedgerEntry.objects.create(
        tenant=tenant, employee=employee, leave_type=leave_type, leave_year=year,
        entry_type=LeaveLedgerEntryType.ADJUSTMENT, days=days, reason=reason, recorded_by=actor or user,
    )
    record_activity(
        tenant=tenant, actor=actor or user, action="leave.balance_adjusted",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"leave_type_id": str(leave_type.id), "year": year, "days": days, "reason": reason},
    )
    return entry


# --- Requests ---------------------------------------------------------------

def _working_days_between(*, tenant, start_date, end_date):
    setup = LeaveSetup.objects.filter(tenant=tenant).first()
    working_days = setup.working_days if setup else default_working_days()
    count = 0
    current = start_date
    while current <= end_date:
        if current.isoweekday() in working_days:
            count += 1
        current += timedelta(days=1)
    return count


def create_leave_request(*, user, tenant, employee, leave_type, start_date, end_date, reason="", actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="leave.request.manage")
    require_same_tenant(tenant=tenant, employee=employee, leave_type=leave_type)
    _require_campus_scope(membership=membership, campus_id=employee.campus_id)

    if start_date > end_date:
        raise ValidationError("start_date must not be after end_date")
    if not leave_type.is_active:
        raise ValidationError("This leave type is not active")

    requested_days = _working_days_between(tenant=tenant, start_date=start_date, end_date=end_date)
    if requested_days <= 0:
        raise ValidationError("This date range contains no working days")

    leave_request = LeaveRequest.objects.create(
        tenant=tenant, employee=employee, leave_type=leave_type, start_date=start_date, end_date=end_date,
        requested_days=requested_days, reason=reason, created_by=actor or user,
    )
    record_activity(
        tenant=tenant, actor=actor or user, action="leave.request.created",
        resource_type="leave_request", resource_id=str(leave_request.id),
    )
    return leave_request


LEAVE_REQUEST_UPDATABLE_FIELDS = ["leave_type", "start_date", "end_date", "reason"]


def update_leave_request(*, user, tenant, leave_request, actor=None, **fields):
    membership = require_permission(user=user, tenant=tenant, permission="leave.request.manage")
    require_same_tenant(tenant=tenant, leave_request=leave_request)
    _require_campus_scope(membership=membership, campus_id=leave_request.employee.campus_id)

    if leave_request.status != LeaveRequestStatus.DRAFT:
        raise ValidationError("Only draft requests can be edited")

    unknown = set(fields) - set(LEAVE_REQUEST_UPDATABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Unsupported field(s): {', '.join(sorted(unknown))}")

    if "leave_type" in fields:
        new_leave_type = fields["leave_type"]
        require_same_tenant(tenant=tenant, leave_type=new_leave_type)
        if not new_leave_type.is_active:
            raise ValidationError("This leave type is not active")

    start_date = fields.get("start_date", leave_request.start_date)
    end_date = fields.get("end_date", leave_request.end_date)
    if start_date > end_date:
        raise ValidationError("start_date must not be after end_date")

    for field, value in fields.items():
        setattr(leave_request, field, value)

    update_fields = list(fields.keys())
    if "start_date" in fields or "end_date" in fields:
        requested_days = _working_days_between(tenant=tenant, start_date=start_date, end_date=end_date)
        if requested_days <= 0:
            raise ValidationError("This date range contains no working days")
        leave_request.requested_days = requested_days
        update_fields.append("requested_days")

    leave_request.save(update_fields=update_fields)
    return leave_request


def _finalize_approval(*, tenant, leave_request, locked_employee, user, actor=None):
    leave_type = leave_request.leave_type
    if leave_type.requires_balance:
        leave_year = resolve_leave_year(tenant=tenant, for_date=leave_request.start_date)
        balance = resolve_leave_balance(tenant=tenant, employee=locked_employee, leave_type=leave_type, year=leave_year)
        if not leave_type.allow_negative_balance and balance < leave_request.requested_days:
            raise ValidationError("Insufficient leave balance to approve this request")
        LeaveLedgerEntry.objects.create(
            tenant=tenant, employee=locked_employee, leave_type=leave_type, leave_year=leave_year,
            entry_type=LeaveLedgerEntryType.CONSUMED, days=-leave_request.requested_days,
            leave_request=leave_request, recorded_by=actor or user,
        )

    leave_request.status = LeaveRequestStatus.APPROVED
    leave_request.save(update_fields=["status"])
    record_activity(
        tenant=tenant, actor=actor or user, action="leave.request.approved",
        resource_type="leave_request", resource_id=str(leave_request.id),
    )
    return leave_request


@transaction.atomic
def submit_leave_request(*, user, tenant, leave_request, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="leave.request.manage")
    require_same_tenant(tenant=tenant, leave_request=leave_request)
    _require_campus_scope(membership=membership, campus_id=leave_request.employee.campus_id)

    locked_employee = Employee.objects.select_for_update().get(tenant=tenant, pk=leave_request.employee_id)
    locked_request = LeaveRequest.objects.select_for_update().get(tenant=tenant, pk=leave_request.pk)

    if locked_request.status != LeaveRequestStatus.DRAFT:
        raise ValidationError("Only draft requests can be submitted")

    overlapping = LeaveRequest.objects.filter(
        tenant=tenant, employee=locked_employee,
        status__in=[LeaveRequestStatus.SUBMITTED, LeaveRequestStatus.APPROVED],
        start_date__lte=locked_request.end_date, end_date__gte=locked_request.start_date,
    ).exclude(pk=locked_request.pk).exists()
    if overlapping:
        raise ValidationError("This employee already has an overlapping leave request")

    leave_type = locked_request.leave_type

    if leave_type.requires_approval:
        workflow = leave_type.approval_workflow
        stages = list(workflow.stages.order_by("sequence")) if workflow is not None else []
        if not stages:
            raise ValidationError("No approval workflow configured for this leave type")

        LeaveRequestApproval.objects.bulk_create([
            LeaveRequestApproval(
                tenant=tenant, leave_request=locked_request, sequence=stage.sequence,
                stage_name=stage.name, approver_role=stage.approver_role,
            )
            for stage in stages
        ])
        locked_request.status = LeaveRequestStatus.SUBMITTED
        locked_request.save(update_fields=["status"])
        record_activity(
            tenant=tenant, actor=actor or user, action="leave.request.submitted",
            resource_type="leave_request", resource_id=str(locked_request.id),
        )
    else:
        _finalize_approval(tenant=tenant, leave_request=locked_request, locked_employee=locked_employee, user=user, actor=actor)

    return locked_request


@transaction.atomic
def decide_leave_request_stage(*, user, tenant, leave_request, decision, comment="", actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="leave.approve")
    require_same_tenant(tenant=tenant, leave_request=leave_request)
    locked_request = LeaveRequest.objects.select_for_update().get(tenant=tenant, pk=leave_request.pk)
    _require_campus_scope(membership=membership, campus_id=locked_request.employee.campus_id)

    if locked_request.status != LeaveRequestStatus.SUBMITTED:
        raise ValidationError("Only submitted requests can be decided")

    stage = LeaveRequestApproval.objects.filter(
        leave_request=locked_request, status=LeaveRequestApprovalStatus.PENDING,
    ).order_by("sequence").first()
    if stage is None:
        raise ValidationError("No pending approval stage for this request")
    if stage.approver_role_id != membership.role_id:
        raise ValidationError("You are not authorized to act on this approval stage")

    if decision == LeaveRequestApprovalStatus.REJECTED:
        stage.status = LeaveRequestApprovalStatus.REJECTED
        stage.decided_by = user
        stage.decided_at = timezone.now()
        stage.comment = comment
        stage.save(update_fields=["status", "decided_by", "decided_at", "comment"])

        LeaveRequestApproval.objects.filter(
            leave_request=locked_request, status=LeaveRequestApprovalStatus.PENDING,
        ).update(status=LeaveRequestApprovalStatus.SKIPPED)

        locked_request.status = LeaveRequestStatus.REJECTED
        locked_request.save(update_fields=["status"])
        record_activity(
            tenant=tenant, actor=actor or user, action="leave.request.rejected",
            resource_type="leave_request", resource_id=str(locked_request.id),
            metadata={"stage": stage.stage_name, "comment": comment} if comment else {"stage": stage.stage_name},
        )
        return locked_request

    if decision != LeaveRequestApprovalStatus.APPROVED:
        raise ValidationError("decision must be APPROVED or REJECTED")

    is_final_stage = not LeaveRequestApproval.objects.filter(
        leave_request=locked_request, status=LeaveRequestApprovalStatus.PENDING,
    ).exclude(pk=stage.pk).exists()

    if is_final_stage:
        locked_employee = Employee.objects.select_for_update().get(tenant=tenant, pk=locked_request.employee_id)
        _finalize_approval(tenant=tenant, leave_request=locked_request, locked_employee=locked_employee, user=user, actor=actor)

    stage.status = LeaveRequestApprovalStatus.APPROVED
    stage.decided_by = user
    stage.decided_at = timezone.now()
    stage.comment = comment
    stage.save(update_fields=["status", "decided_by", "decided_at", "comment"])

    if not is_final_stage:
        record_activity(
            tenant=tenant, actor=actor or user, action="leave.request.stage_approved",
            resource_type="leave_request", resource_id=str(locked_request.id),
            metadata={"stage": stage.stage_name},
        )

    return locked_request


@transaction.atomic
def withdraw_leave_request(*, user, tenant, leave_request, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="leave.request.manage")
    require_same_tenant(tenant=tenant, leave_request=leave_request)
    locked_request = LeaveRequest.objects.select_for_update().get(tenant=tenant, pk=leave_request.pk)
    _require_campus_scope(membership=membership, campus_id=locked_request.employee.campus_id)

    if locked_request.status != LeaveRequestStatus.SUBMITTED:
        raise ValidationError("Only submitted requests can be withdrawn")

    locked_request.status = LeaveRequestStatus.CANCELLED
    locked_request.save(update_fields=["status"])
    record_activity(
        tenant=tenant, actor=actor or user, action="leave.request.withdrawn",
        resource_type="leave_request", resource_id=str(locked_request.id),
    )
    return locked_request


@transaction.atomic
def cancel_approved_leave_request(*, user, tenant, leave_request, reason="", actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="leave.request.manage")
    require_same_tenant(tenant=tenant, leave_request=leave_request)
    locked_request = LeaveRequest.objects.select_for_update().get(tenant=tenant, pk=leave_request.pk)
    _require_campus_scope(membership=membership, campus_id=locked_request.employee.campus_id)

    if locked_request.status != LeaveRequestStatus.APPROVED:
        raise ValidationError("Only approved requests can be cancelled")

    if locked_request.leave_type.requires_balance:
        locked_employee = Employee.objects.select_for_update().get(tenant=tenant, pk=locked_request.employee_id)
        leave_year = resolve_leave_year(tenant=tenant, for_date=locked_request.start_date)
        LeaveLedgerEntry.objects.create(
            tenant=tenant, employee=locked_employee, leave_type=locked_request.leave_type, leave_year=leave_year,
            entry_type=LeaveLedgerEntryType.REVERSAL, days=locked_request.requested_days,
            leave_request=locked_request, reason=reason, recorded_by=actor or user,
        )

    locked_request.status = LeaveRequestStatus.CANCELLED
    locked_request.save(update_fields=["status"])
    record_activity(
        tenant=tenant, actor=actor or user, action="leave.request.cancelled",
        resource_type="leave_request", resource_id=str(locked_request.id),
        metadata={"reason": reason} if reason else {},
    )
    return locked_request


def resolve_employee_leave_requests(*, tenant, employee):
    return LeaveRequest.objects.filter(tenant=tenant, employee=employee).order_by("-created_at")
