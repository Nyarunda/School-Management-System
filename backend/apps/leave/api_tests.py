from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from apps.staff.services import create_employee
from apps.tenancy.models import Membership, Role, Tenant, User


class LeaveApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="HR Admin",
            permissions=[
                "staff.manage", "leave.setup.view", "leave.setup.manage", "leave.request.view",
                "leave.request.manage", "leave.balance.adjust",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.admin_role)

        self.supervisor_role = Role.objects.create(tenant=self.school_a, name="Supervisor", permissions=["leave.approve"])
        self.supervisor = User.objects.create_user(username="supervisor", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.supervisor, role=self.supervisor_role)

        self.employee = create_employee(
            user=self.admin, tenant=self.school_a, employee_number="EMP-001", first_name="Jane", last_name="Doe",
            job_title="Teacher", employment_type="PERMANENT", hire_date=date(2020, 1, 1),
        )
        self.client.force_authenticate(self.admin)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_missing_tenant_header_is_rejected(self):
        response = self.client.get("/api/v1/leave/requests/")
        self.assertEqual(response.status_code, 404)

    def test_disabling_the_staff_hr_module_blocks_access_even_with_permission(self):
        from apps.platform.services import set_module_override

        set_module_override(tenant=self.school_a, module_code="staff_hr", is_enabled=False)
        response = self.client.get("/api/v1/leave/requests/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_leave_setup_get_and_patch(self):
        get_response = self.client.get("/api/v1/leave/setup/", **self.headers())
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.data["leave_year_start_month"], 1)

        patch_response = self.client.patch(
            "/api/v1/leave/setup/", {"leave_year_start_month": 7, "leave_year_start_day": 1}, format="json", **self.headers(),
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data["leave_year_start_month"], 7)

    def create_workflow_with_stage(self):
        workflow_response = self.client.post("/api/v1/leave/workflows/", {"name": "Standard"}, format="json", **self.headers())
        workflow_id = workflow_response.data["id"]
        stage_response = self.client.post(
            f"/api/v1/leave/workflows/{workflow_id}/stages/",
            {"sequence": 1, "name": "Supervisor", "approver_role": self.supervisor_role.id},
            format="json", **self.headers(),
        )
        return workflow_id, stage_response.data["id"]

    def test_workflow_and_stage_crud(self):
        workflow_id, stage_id = self.create_workflow_with_stage()

        list_response = self.client.get(f"/api/v1/leave/workflows/{workflow_id}/stages/", **self.headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

        patch_response = self.client.patch(
            f"/api/v1/leave/workflows/{workflow_id}/stages/{stage_id}/", {"name": "Line Manager"}, format="json", **self.headers(),
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data["name"], "Line Manager")

        delete_response = self.client.delete(f"/api/v1/leave/workflows/{workflow_id}/stages/{stage_id}/", **self.headers())
        self.assertEqual(delete_response.status_code, 204)

    def create_leave_type_via_api(self, **overrides):
        payload = {"name": "Annual Leave", "code": "ANNUAL", "default_annual_entitlement_days": 21}
        payload.update(overrides)
        return self.client.post("/api/v1/leave/types/", payload, format="json", **self.headers())

    def test_leave_type_crud_and_carry_forward_validation(self):
        create_response = self.create_leave_type_via_api()
        self.assertEqual(create_response.status_code, 201)
        leave_type_id = create_response.data["id"]

        bad_response = self.client.patch(
            f"/api/v1/leave/types/{leave_type_id}/", {"allows_carry_forward": True}, format="json", **self.headers(),
        )
        self.assertEqual(bad_response.status_code, 400)

        good_response = self.client.patch(
            f"/api/v1/leave/types/{leave_type_id}/",
            {"allows_carry_forward": True, "max_carry_forward_days": 5}, format="json", **self.headers(),
        )
        self.assertEqual(good_response.status_code, 200)
        self.assertEqual(good_response.data["max_carry_forward_days"], 5)

    def test_full_request_lifecycle_via_http(self):
        workflow_id, _ = self.create_workflow_with_stage()
        type_response = self.create_leave_type_via_api(approval_workflow=workflow_id)
        leave_type_id = type_response.data["id"]

        entitlement_response = self.client.post(
            f"/api/v1/leave/employees/{self.employee.id}/entitlement/",
            {"leave_type": leave_type_id, "year": 2026}, format="json", **self.headers(),
        )
        self.assertEqual(entitlement_response.status_code, 201)

        create_response = self.client.post(
            "/api/v1/leave/requests/",
            {"employee": str(self.employee.id), "leave_type": leave_type_id, "start_date": "2026-01-05", "end_date": "2026-01-09"},
            format="json", **self.headers(),
        )
        self.assertEqual(create_response.status_code, 201)
        request_id = create_response.data["id"]
        self.assertEqual(create_response.data["requested_days"], 5)

        submit_response = self.client.post(f"/api/v1/leave/requests/{request_id}/submit/", **self.headers())
        self.assertEqual(submit_response.status_code, 200)
        self.assertEqual(submit_response.data["status"], "SUBMITTED")

        self.client.force_authenticate(self.supervisor)
        decide_response = self.client.post(
            f"/api/v1/leave/requests/{request_id}/decide/", {"decision": "APPROVED"}, format="json", **self.headers(),
        )
        self.assertEqual(decide_response.status_code, 200)
        self.assertEqual(decide_response.data["status"], "APPROVED")

        self.client.force_authenticate(self.admin)
        balance_response = self.client.get(
            f"/api/v1/leave/employees/{self.employee.id}/balance/?leave_type={leave_type_id}&year=2026", **self.headers(),
        )
        self.assertEqual(balance_response.status_code, 200)
        self.assertEqual(balance_response.data["balance"], 16)

        cancel_response = self.client.post(
            f"/api/v1/leave/requests/{request_id}/cancel/", {"reason": "Plans changed"}, format="json", **self.headers(),
        )
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.data["status"], "CANCELLED")

    def test_reject_path_via_http(self):
        workflow_id, _ = self.create_workflow_with_stage()
        type_response = self.create_leave_type_via_api(code="REJ", approval_workflow=workflow_id)
        leave_type_id = type_response.data["id"]

        create_response = self.client.post(
            "/api/v1/leave/requests/",
            {"employee": str(self.employee.id), "leave_type": leave_type_id, "start_date": "2026-01-05", "end_date": "2026-01-06"},
            format="json", **self.headers(),
        )
        request_id = create_response.data["id"]
        self.client.post(f"/api/v1/leave/requests/{request_id}/submit/", **self.headers())

        self.client.force_authenticate(self.supervisor)
        decide_response = self.client.post(
            f"/api/v1/leave/requests/{request_id}/decide/", {"decision": "REJECTED", "comment": "Not now"}, format="json", **self.headers(),
        )
        self.assertEqual(decide_response.status_code, 200)
        self.assertEqual(decide_response.data["status"], "REJECTED")

    def test_withdraw_flow(self):
        workflow_id, _ = self.create_workflow_with_stage()
        type_response = self.create_leave_type_via_api(code="WD", approval_workflow=workflow_id)
        leave_type_id = type_response.data["id"]

        create_response = self.client.post(
            "/api/v1/leave/requests/",
            {"employee": str(self.employee.id), "leave_type": leave_type_id, "start_date": "2026-01-05", "end_date": "2026-01-06"},
            format="json", **self.headers(),
        )
        request_id = create_response.data["id"]
        self.client.post(f"/api/v1/leave/requests/{request_id}/submit/", **self.headers())

        withdraw_response = self.client.post(f"/api/v1/leave/requests/{request_id}/withdraw/", **self.headers())
        self.assertEqual(withdraw_response.status_code, 200)
        self.assertEqual(withdraw_response.data["status"], "CANCELLED")

    def test_leave_approve_permission_alone_is_not_sufficient_without_matching_role(self):
        workflow_id, _ = self.create_workflow_with_stage()
        type_response = self.create_leave_type_via_api(code="RBAC", approval_workflow=workflow_id)
        leave_type_id = type_response.data["id"]

        create_response = self.client.post(
            "/api/v1/leave/requests/",
            {"employee": str(self.employee.id), "leave_type": leave_type_id, "start_date": "2026-01-05", "end_date": "2026-01-06"},
            format="json", **self.headers(),
        )
        request_id = create_response.data["id"]
        self.client.post(f"/api/v1/leave/requests/{request_id}/submit/", **self.headers())

        other_approver_role = Role.objects.create(tenant=self.school_a, name="Other Approver", permissions=["leave.approve"])
        other_approver = User.objects.create_user(username="other-approver", password="secret")
        Membership.objects.create(tenant=self.school_a, user=other_approver, role=other_approver_role)
        self.client.force_authenticate(other_approver)

        decide_response = self.client.post(
            f"/api/v1/leave/requests/{request_id}/decide/", {"decision": "APPROVED"}, format="json", **self.headers(),
        )
        self.assertEqual(decide_response.status_code, 400)

    def test_request_list_is_paginated_and_filterable(self):
        leave_type_response = self.create_leave_type_via_api(code="LIST", requires_approval=False)
        leave_type_id = leave_type_response.data["id"]
        self.client.post(
            "/api/v1/leave/requests/",
            {"employee": str(self.employee.id), "leave_type": leave_type_id, "start_date": "2026-01-05", "end_date": "2026-01-06"},
            format="json", **self.headers(),
        )
        response = self.client.get(f"/api/v1/leave/requests/?employee={self.employee.id}&status=DRAFT", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_adjustment_endpoint(self):
        type_response = self.create_leave_type_via_api(code="ADJ")
        leave_type_id = type_response.data["id"]
        response = self.client.post(
            f"/api/v1/leave/employees/{self.employee.id}/adjustments/",
            {"leave_type": leave_type_id, "year": 2026, "days": -2, "reason": "Correction"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["days"], -2)
