from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.activity.models import ActivityEvent
from apps.notifications.models import NotificationChannel, NotificationOutbox, NotificationRecipientType
from apps.notifications.services import create_notification_rule, create_notification_template, set_channel_enabled
from apps.staff.services import create_employee
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import (
    LeaveLedgerEntryType,
    LeaveRequestApprovalStatus,
    LeaveRequestStatus,
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
    resolve_leave_year,
    submit_leave_request,
    update_leave_type,
    update_workflow_stage,
    withdraw_leave_request,
)


class LeaveFoundationTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="HR Admin",
            permissions=[
                "staff.manage", "leave.setup.view", "leave.setup.manage", "leave.request.view",
                "leave.request.manage", "leave.balance.adjust",
                "notifications.setup.manage", "notifications.templates.manage", "notifications.rules.manage",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.admin_role)

        self.supervisor_role = Role.objects.create(tenant=self.school_a, name="Supervisor", permissions=["leave.approve"])
        self.supervisor = User.objects.create_user(username="supervisor", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.supervisor, role=self.supervisor_role)

        self.hr_role = Role.objects.create(tenant=self.school_a, name="HR Approver", permissions=["leave.approve"])
        self.hr_approver = User.objects.create_user(username="hr-approver", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.hr_approver, role=self.hr_role)

        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.other_campus = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")

        self.employee = create_employee(
            user=self.admin, tenant=self.school_a, employee_number="EMP-001", first_name="Jane", last_name="Doe",
            job_title="Teacher", employment_type="PERMANENT", hire_date=date(2020, 1, 1), phone_number="0711111111",
        )

    def make_workflow(self, *, single_stage=False):
        workflow = create_leave_workflow(user=self.admin, tenant=self.school_a, name="Standard")
        add_workflow_stage(
            user=self.admin, tenant=self.school_a, workflow=workflow, sequence=1, name="Supervisor",
            approver_role=self.supervisor_role,
        )
        if not single_stage:
            add_workflow_stage(
                user=self.admin, tenant=self.school_a, workflow=workflow, sequence=2, name="HR",
                approver_role=self.hr_role,
            )
        return workflow

    def make_leave_type(self, **overrides):
        values = dict(
            user=self.admin, tenant=self.school_a, name="Annual Leave", code="ANNUAL",
            default_annual_entitlement_days=21, allows_carry_forward=True, max_carry_forward_days=5,
        )
        values.update(overrides)
        return create_leave_type(**values)

    def grant(self, leave_type, year, days=None):
        return grant_leave_entitlement(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=year, days=days,
        )


class LeaveSetupTests(LeaveFoundationTests):
    def test_configure_leave_setup_creates_and_updates(self):
        setup = configure_leave_setup(user=self.admin, tenant=self.school_a, working_days=[1, 2, 3, 4, 5, 6])
        self.assertEqual(setup.working_days, [1, 2, 3, 4, 5, 6])
        updated = configure_leave_setup(user=self.admin, tenant=self.school_a, leave_year_start_month=7, leave_year_start_day=1)
        self.assertEqual(updated.leave_year_start_month, 7)
        self.assertEqual(updated.working_days, [1, 2, 3, 4, 5, 6])

    def test_invalid_month_is_rejected(self):
        with self.assertRaises(ValidationError):
            configure_leave_setup(user=self.admin, tenant=self.school_a, leave_year_start_month=13)

    def test_invalid_day_is_rejected(self):
        with self.assertRaises(ValidationError):
            configure_leave_setup(user=self.admin, tenant=self.school_a, leave_year_start_day=30)


class LeaveYearResolutionTests(LeaveFoundationTests):
    def test_unconfigured_tenant_behaves_like_calendar_year(self):
        self.assertEqual(resolve_leave_year(tenant=self.school_a, for_date=date(2026, 3, 15)), 2026)

    def test_non_default_anchor_buckets_dates_on_either_side(self):
        configure_leave_setup(user=self.admin, tenant=self.school_a, leave_year_start_month=7, leave_year_start_day=1)
        self.assertEqual(resolve_leave_year(tenant=self.school_a, for_date=date(2026, 8, 15)), 2026)
        self.assertEqual(resolve_leave_year(tenant=self.school_a, for_date=date(2026, 3, 15)), 2025)
        self.assertEqual(resolve_leave_year(tenant=self.school_a, for_date=date(2026, 7, 1)), 2026)
        self.assertEqual(resolve_leave_year(tenant=self.school_a, for_date=date(2026, 6, 30)), 2025)


class LeaveTypeTests(LeaveFoundationTests):
    def test_carry_forward_requires_max_days(self):
        with self.assertRaisesMessage(ValidationError, "max_carry_forward_days is required"):
            self.make_leave_type(code="X1", allows_carry_forward=True, max_carry_forward_days=None)

    def test_max_days_forbidden_without_carry_forward(self):
        with self.assertRaisesMessage(ValidationError, "must be empty"):
            self.make_leave_type(code="X2", allows_carry_forward=False, max_carry_forward_days=5)

    def test_update_leave_type_revalidates_carry_forward_pair(self):
        leave_type = self.make_leave_type(code="X3")
        with self.assertRaises(ValidationError):
            update_leave_type(user=self.admin, tenant=self.school_a, leave_type=leave_type, max_carry_forward_days=None, allows_carry_forward=True)

    def test_unique_code_per_tenant(self):
        self.make_leave_type(code="DUP")
        with self.assertRaises(Exception):
            self.make_leave_type(code="DUP")


class WorkflowStageTests(LeaveFoundationTests):
    def test_stages_are_ordered_and_editable(self):
        workflow = self.make_workflow()
        stages = list(workflow.stages.order_by("sequence"))
        self.assertEqual([s.name for s in stages], ["Supervisor", "HR"])

        updated = update_workflow_stage(user=self.admin, tenant=self.school_a, stage=stages[0], name="Line Manager")
        self.assertEqual(updated.name, "Line Manager")

    def test_stage_can_be_deleted_without_affecting_future_lookups(self):
        workflow = self.make_workflow()
        stage = workflow.stages.get(sequence=2)
        delete_workflow_stage(user=self.admin, tenant=self.school_a, stage=stage)
        self.assertEqual(workflow.stages.count(), 1)


class WorkingDaysTests(LeaveFoundationTests):
    def test_requested_days_excludes_weekends_by_default(self):
        leave_type = self.make_leave_type(code="WD1")
        # Monday 2026-01-05 to Friday 2026-01-09 inclusive = 5 working days
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 9),
        )
        self.assertEqual(request.requested_days, 5)

    def test_weekend_only_range_is_rejected(self):
        leave_type = self.make_leave_type(code="WD2")
        with self.assertRaisesMessage(ValidationError, "no working days"):
            create_leave_request(
                user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
                start_date=date(2026, 1, 10), end_date=date(2026, 1, 11),
            )

    def test_custom_working_days_are_respected(self):
        configure_leave_setup(user=self.admin, tenant=self.school_a, working_days=[6, 7])
        leave_type = self.make_leave_type(code="WD3")
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 11),
        )
        self.assertEqual(request.requested_days, 2)


class SubmitLeaveRequestTests(LeaveFoundationTests):
    def test_submit_requires_an_approval_chain(self):
        leave_type = self.make_leave_type(code="NOFLOW")
        self.grant(leave_type, 2026)
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
        )
        with self.assertRaisesMessage(ValidationError, "No approval workflow"):
            submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)

    def test_submit_creates_pending_stage_snapshot(self):
        workflow = self.make_workflow()
        leave_type = self.make_leave_type(code="SUB1", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
        )
        submitted = submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)
        self.assertEqual(submitted.status, LeaveRequestStatus.SUBMITTED)
        self.assertEqual(submitted.approvals.count(), 2)
        self.assertTrue(all(a.status == LeaveRequestApprovalStatus.PENDING for a in submitted.approvals.all()))

    def test_overlapping_submitted_request_is_rejected(self):
        workflow = self.make_workflow(single_stage=True)
        leave_type = self.make_leave_type(code="OVL", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        first = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 9),
        )
        submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=first)

        second = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 8), end_date=date(2026, 1, 12),
        )
        with self.assertRaisesMessage(ValidationError, "overlapping"):
            submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=second)

    def test_only_draft_requests_can_be_submitted(self):
        workflow = self.make_workflow(single_stage=True)
        leave_type = self.make_leave_type(code="DRFT", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
        )
        submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)
        with self.assertRaisesMessage(ValidationError, "Only draft"):
            submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)


class DecisionFlowTests(LeaveFoundationTests):
    def submit(self, leave_type, start=date(2026, 1, 5), end=date(2026, 1, 9)):
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=start, end_date=end,
        )
        return submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)

    def test_partial_approval_leaves_request_submitted(self):
        workflow = self.make_workflow()
        leave_type = self.make_leave_type(code="PART", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = self.submit(leave_type)

        decided = decide_leave_request_stage(
            user=self.supervisor, tenant=self.school_a, leave_request=request, decision=LeaveRequestApprovalStatus.APPROVED,
        )
        self.assertEqual(decided.status, LeaveRequestStatus.SUBMITTED)
        self.assertEqual(decided.approvals.get(sequence=1).status, LeaveRequestApprovalStatus.APPROVED)
        self.assertEqual(decided.approvals.get(sequence=2).status, LeaveRequestApprovalStatus.PENDING)

    def test_final_approval_posts_exactly_one_consumed_entry(self):
        workflow = self.make_workflow()
        leave_type = self.make_leave_type(code="FULL", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = self.submit(leave_type)

        decide_leave_request_stage(user=self.supervisor, tenant=self.school_a, leave_request=request, decision=LeaveRequestApprovalStatus.APPROVED)
        approved = decide_leave_request_stage(user=self.hr_approver, tenant=self.school_a, leave_request=request, decision=LeaveRequestApprovalStatus.APPROVED)

        self.assertEqual(approved.status, LeaveRequestStatus.APPROVED)
        consumed = approved.ledger_entries.filter(entry_type=LeaveLedgerEntryType.CONSUMED)
        self.assertEqual(consumed.count(), 1)
        self.assertEqual(consumed.first().days, -5)
        self.assertEqual(resolve_leave_balance(tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026), 16)

    def test_final_approval_notifies_the_employee_when_configured(self):
        set_channel_enabled(user=self.admin, tenant=self.school_a, channel=NotificationChannel.SMS, enabled=True)
        template = create_notification_template(
            user=self.admin, tenant=self.school_a, code="LEAVE_APPROVED_SMS", name="Leave approved", channel=NotificationChannel.SMS,
            body="Dear {{ employee_name }}, your leave from {{ start_date }} to {{ end_date }} was approved.",
        )
        create_notification_rule(
            user=self.admin, tenant=self.school_a, event_code="leave.request.approved",
            recipient_type=NotificationRecipientType.EMPLOYEE, channel=NotificationChannel.SMS, template=template,
        )
        workflow = self.make_workflow(single_stage=True)
        leave_type = self.make_leave_type(code="NOTIFY", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = self.submit(leave_type)

        decide_leave_request_stage(user=self.supervisor, tenant=self.school_a, leave_request=request, decision=LeaveRequestApprovalStatus.APPROVED)

        outbox = NotificationOutbox.objects.get(tenant=self.school_a)
        self.assertEqual(outbox.recipient, "0711111111")
        self.assertIn("Jane Doe", outbox.context["body"])

    def test_middle_stage_rejection_skips_later_stages_and_posts_no_ledger(self):
        workflow = self.make_workflow()
        leave_type = self.make_leave_type(code="REJ", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = self.submit(leave_type)

        rejected = decide_leave_request_stage(
            user=self.supervisor, tenant=self.school_a, leave_request=request, decision=LeaveRequestApprovalStatus.REJECTED,
            comment="Not approved",
        )
        self.assertEqual(rejected.status, LeaveRequestStatus.REJECTED)
        self.assertFalse(rejected.ledger_entries.exists())
        self.assertEqual(resolve_leave_balance(tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026), 21)

    def test_wrong_role_cannot_decide_a_stage(self):
        workflow = self.make_workflow()
        leave_type = self.make_leave_type(code="WRNG", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = self.submit(leave_type)

        with self.assertRaisesMessage(ValidationError, "not authorized"):
            decide_leave_request_stage(user=self.hr_approver, tenant=self.school_a, leave_request=request, decision=LeaveRequestApprovalStatus.APPROVED)

    def test_insufficient_balance_rejects_final_approval_and_leaves_stage_untouched(self):
        workflow = self.make_workflow(single_stage=True)
        leave_type = self.make_leave_type(code="POOR", approval_workflow=workflow)
        self.grant(leave_type, 2026, days=2)
        request = self.submit(leave_type)  # requests 5 working days, only 2 granted

        with self.assertRaisesMessage(ValidationError, "Insufficient leave balance"):
            decide_leave_request_stage(user=self.supervisor, tenant=self.school_a, leave_request=request, decision=LeaveRequestApprovalStatus.APPROVED)

        request.refresh_from_db()
        self.assertEqual(request.status, LeaveRequestStatus.SUBMITTED)
        self.assertEqual(request.approvals.get(sequence=1).status, LeaveRequestApprovalStatus.PENDING)

    def test_allow_negative_balance_permits_approval_past_zero(self):
        workflow = self.make_workflow(single_stage=True)
        leave_type = self.make_leave_type(code="NEG", approval_workflow=workflow, allow_negative_balance=True)
        self.grant(leave_type, 2026, days=1)
        request = self.submit(leave_type)

        approved = decide_leave_request_stage(user=self.supervisor, tenant=self.school_a, leave_request=request, decision=LeaveRequestApprovalStatus.APPROVED)
        self.assertEqual(approved.status, LeaveRequestStatus.APPROVED)
        self.assertEqual(resolve_leave_balance(tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026), -4)


class RequiresApprovalFalseTests(LeaveFoundationTests):
    def test_self_approving_type_goes_straight_to_approved(self):
        leave_type = self.make_leave_type(code="SELF", requires_approval=False, allows_carry_forward=False, max_carry_forward_days=None)
        self.grant(leave_type, 2026)
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
        )
        submitted = submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)
        self.assertEqual(submitted.status, LeaveRequestStatus.APPROVED)
        self.assertEqual(submitted.approvals.count(), 0)


class RequiresBalanceFalseTests(LeaveFoundationTests):
    def test_unpaid_leave_type_posts_no_ledger_entries(self):
        workflow = self.make_workflow(single_stage=True)
        leave_type = self.make_leave_type(
            code="UNPAID", requires_balance=False, allows_carry_forward=False, max_carry_forward_days=None,
            default_annual_entitlement_days=0, approval_workflow=workflow,
        )
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
        )
        submitted = submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)
        approved = decide_leave_request_stage(user=self.supervisor, tenant=self.school_a, leave_request=submitted, decision=LeaveRequestApprovalStatus.APPROVED)
        self.assertEqual(approved.status, LeaveRequestStatus.APPROVED)
        self.assertFalse(approved.ledger_entries.exists())

        cancel_approved_leave_request(user=self.admin, tenant=self.school_a, leave_request=approved, reason="Changed plans")
        self.assertFalse(approved.ledger_entries.exists())

    def test_balance_mutations_reject_against_a_no_balance_type(self):
        leave_type = self.make_leave_type(code="NB", requires_balance=False, allows_carry_forward=False, max_carry_forward_days=None)
        with self.assertRaisesMessage(ValidationError, "does not track a balance"):
            self.grant(leave_type, 2026)


class WithdrawAndCancelTests(LeaveFoundationTests):
    def test_withdraw_from_submitted_has_no_ledger_effect(self):
        workflow = self.make_workflow(single_stage=True)
        leave_type = self.make_leave_type(code="WD", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
        )
        submitted = submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)
        withdrawn = withdraw_leave_request(user=self.admin, tenant=self.school_a, leave_request=submitted)
        self.assertEqual(withdrawn.status, LeaveRequestStatus.CANCELLED)
        self.assertEqual(resolve_leave_balance(tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026), 21)

    def test_cancel_approved_posts_a_reversal_not_a_mutation(self):
        workflow = self.make_workflow(single_stage=True)
        leave_type = self.make_leave_type(code="CANC", approval_workflow=workflow)
        self.grant(leave_type, 2026)
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 9),
        )
        submitted = submit_leave_request(user=self.admin, tenant=self.school_a, leave_request=request)
        approved = decide_leave_request_stage(user=self.supervisor, tenant=self.school_a, leave_request=submitted, decision=LeaveRequestApprovalStatus.APPROVED)

        cancelled = cancel_approved_leave_request(user=self.admin, tenant=self.school_a, leave_request=approved, reason="Plans changed")
        self.assertEqual(cancelled.status, LeaveRequestStatus.CANCELLED)
        self.assertEqual(approved.ledger_entries.count(), 2)
        original = approved.ledger_entries.get(entry_type=LeaveLedgerEntryType.CONSUMED)
        self.assertEqual(original.days, -5)
        reversal = approved.ledger_entries.get(entry_type=LeaveLedgerEntryType.REVERSAL)
        self.assertEqual(reversal.days, 5)
        self.assertEqual(resolve_leave_balance(tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026), 21)

    def test_cannot_cancel_a_non_approved_request(self):
        leave_type = self.make_leave_type(code="NC")
        request = create_leave_request(
            user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
        )
        with self.assertRaisesMessage(ValidationError, "Only approved"):
            cancel_approved_leave_request(user=self.admin, tenant=self.school_a, leave_request=request, reason="x")


class BalanceMutationTests(LeaveFoundationTests):
    def test_grant_entitlement_defaults_to_leave_type_amount(self):
        leave_type = self.make_leave_type(code="GNT")
        entry = self.grant(leave_type, 2026)
        self.assertEqual(entry.days, 21)

    def test_carry_forward_is_capped_and_rejected_when_disallowed(self):
        leave_type = self.make_leave_type(code="CF1", max_carry_forward_days=3)
        self.grant(leave_type, 2025, days=10)
        entry = carry_forward_leave(user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type, from_year=2025, to_year=2026)
        self.assertEqual(entry.days, 3)
        self.assertEqual(resolve_leave_balance(tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2025), 10)
        self.assertEqual(resolve_leave_balance(tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026), 3)

        no_carry_type = self.make_leave_type(code="CF2", allows_carry_forward=False, max_carry_forward_days=None)
        self.grant(no_carry_type, 2025)
        with self.assertRaisesMessage(ValidationError, "does not allow carry-forward"):
            carry_forward_leave(user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=no_carry_type, from_year=2025, to_year=2026)

    def test_adjust_balance_requires_reason_and_nonzero_days(self):
        leave_type = self.make_leave_type(code="ADJ")
        with self.assertRaises(ValidationError):
            adjust_leave_balance(user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026, days=2, reason="")
        with self.assertRaises(ValidationError):
            adjust_leave_balance(user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026, days=0, reason="fix")

        entry = adjust_leave_balance(user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=leave_type, year=2026, days=-2, reason="Correction")
        self.assertEqual(entry.days, -2)


class CampusScopingTests(LeaveFoundationTests):
    def setUp(self):
        super().setUp()
        self.scoped_admin = User.objects.create_user(username="scoped-admin", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.scoped_admin, role=self.admin_role, campus=self.campus)
        self.other_campus_employee = create_employee(
            user=self.admin, tenant=self.school_a, employee_number="EMP-002", first_name="Sam", last_name="Smith",
            job_title="Teacher", employment_type="PERMANENT", hire_date=date(2020, 1, 1), campus=self.other_campus,
        )

    def test_campus_scoped_actor_cannot_create_request_for_a_different_campus_employee(self):
        leave_type = self.make_leave_type(code="CS1")
        with self.assertRaises(ValidationError):
            create_leave_request(
                user=self.scoped_admin, tenant=self.school_a, employee=self.other_campus_employee, leave_type=leave_type,
                start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
            )


class CrossTenantTests(LeaveFoundationTests):
    def test_leave_type_from_another_tenant_is_rejected(self):
        other_admin = User.objects.create_user(username="other-admin", password="secret")
        other_role = Role.objects.create(tenant=self.school_b, name="HR", permissions=["leave.setup.manage", "staff.manage"])
        Membership.objects.create(tenant=self.school_b, user=other_admin, role=other_role)
        other_leave_type = create_leave_type(
            user=other_admin, tenant=self.school_b, name="Annual", code="ANNUAL", default_annual_entitlement_days=21,
        )
        with self.assertRaises(ValidationError):
            create_leave_request(
                user=self.admin, tenant=self.school_a, employee=self.employee, leave_type=other_leave_type,
                start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
            )
