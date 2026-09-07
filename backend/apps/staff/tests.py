from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.activity.models import ActivityEvent
from apps.documents.testing import TemporaryDocumentStorageMixin, make_upload
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Employee, EmploymentStatus
from .services import (
    add_employee_document,
    add_employee_qualification,
    change_employment_status,
    create_employee,
    delete_employee_document,
    link_user_account,
    unlink_user_account,
    update_employee_details,
)


class StaffFoundationTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="HR Officer",
            permissions=["staff.view", "staff.manage", "staff.user_link.manage"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.admin_role)

        self.viewer = User.objects.create_user(username="viewer", password="secret")
        self.viewer_role = Role.objects.create(tenant=self.school_a, name="Viewer", permissions=["staff.view"])
        Membership.objects.create(tenant=self.school_a, user=self.viewer, role=self.viewer_role)

        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.other_campus = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")
        self.other_campus_school_b = Campus.objects.create(tenant=self.school_b, name="Other", code="OTHER")

        self.scoped_admin = User.objects.create_user(username="scoped-admin", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.scoped_admin, role=self.admin_role, campus=self.campus)

    def make_employee(self, **overrides):
        values = dict(
            user=self.admin, tenant=self.school_a, employee_number="EMP-001", first_name="Jane",
            last_name="Doe", job_title="Teacher", employment_type="PERMANENT", hire_date=date(2024, 1, 1),
        )
        values.update(overrides)
        return create_employee(**values)


class CreateEmployeeTests(StaffFoundationTests):
    def test_creates_employee(self):
        employee = self.make_employee()
        self.assertEqual(employee.status, EmploymentStatus.ACTIVE)
        self.assertEqual(employee.full_name, "Jane Doe")
        event = ActivityEvent.objects.get(action="staff.employee.created")
        self.assertEqual(event.resource_id, str(employee.id))

    def test_requires_permission(self):
        with self.assertRaises(ValidationError):
            create_employee(
                user=self.viewer, tenant=self.school_a, employee_number="EMP-002", first_name="A", last_name="B",
                job_title="Teacher", employment_type="PERMANENT", hire_date=date(2024, 1, 1),
            )

    def test_cross_tenant_campus_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.make_employee(campus=self.other_campus_school_b)

    def test_duplicate_employee_number_within_tenant_is_a_clean_validation_error(self):
        self.make_employee()
        with self.assertRaisesMessage(ValidationError, "already in use"):
            self.make_employee(first_name="Other")

    def test_same_employee_number_in_different_tenants_is_allowed(self):
        self.make_employee()
        other_admin = User.objects.create_user(username="other-admin", password="secret")
        other_role = Role.objects.create(tenant=self.school_b, name="HR", permissions=["staff.manage"])
        Membership.objects.create(tenant=self.school_b, user=other_admin, role=other_role)
        employee = create_employee(
            user=other_admin, tenant=self.school_b, employee_number="EMP-001", first_name="Copy", last_name="Cat",
            job_title="Teacher", employment_type="PERMANENT", hire_date=date(2024, 1, 1),
        )
        self.assertIsNotNone(employee.pk)

    def test_employee_number_is_normalized_and_collides_regardless_of_case_or_whitespace(self):
        self.make_employee(employee_number="EMP001")
        with self.assertRaisesMessage(ValidationError, "already in use"):
            self.make_employee(employee_number=" emp001 ", first_name="Other")

    def test_blank_after_normalization_employee_number_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "cannot be blank"):
            self.make_employee(employee_number="   ")

    def test_date_of_birth_after_hire_date_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "before the hire date"):
            self.make_employee(date_of_birth=date(2024, 6, 1), hire_date=date(2024, 1, 1))

    def test_campus_scoped_actor_can_create_within_own_campus(self):
        employee = self.make_employee(user=self.scoped_admin, campus=self.campus)
        self.assertEqual(employee.campus_id, self.campus.id)

    def test_campus_scoped_actor_cannot_create_at_a_different_campus(self):
        with self.assertRaises(ValidationError):
            self.make_employee(user=self.scoped_admin, campus=self.other_campus)

    def test_campus_scoped_actor_cannot_create_an_unscoped_employee(self):
        with self.assertRaises(ValidationError):
            self.make_employee(user=self.scoped_admin)


class UpdateEmployeeDetailsTests(StaffFoundationTests):
    def test_updates_changed_fields_and_audits(self):
        employee = self.make_employee()
        updated = update_employee_details(user=self.admin, tenant=self.school_a, employee=employee, job_title="Head Teacher")
        self.assertEqual(updated.job_title, "Head Teacher")
        event = ActivityEvent.objects.get(action="staff.employee.updated")
        self.assertEqual(event.metadata["previous"]["job_title"], "Teacher")
        self.assertEqual(event.metadata["new"]["job_title"], "Head Teacher")

    def test_noop_update_is_not_audited(self):
        employee = self.make_employee()
        ActivityEvent.objects.filter(action="staff.employee.created").delete()
        update_employee_details(user=self.admin, tenant=self.school_a, employee=employee, job_title="Teacher")
        self.assertFalse(ActivityEvent.objects.filter(action="staff.employee.updated").exists())

    def test_campus_change_validates_same_tenant(self):
        employee = self.make_employee()
        with self.assertRaises(ValidationError):
            update_employee_details(user=self.admin, tenant=self.school_a, employee=employee, campus=self.other_campus_school_b)

    def test_unknown_field_is_rejected(self):
        employee = self.make_employee()
        with self.assertRaisesMessage(ValidationError, "Unsupported field"):
            update_employee_details(user=self.admin, tenant=self.school_a, employee=employee, employee_number="EMP-999")

    def test_date_of_birth_after_hire_date_is_rejected_on_update(self):
        employee = self.make_employee(date_of_birth=date(1990, 1, 1))
        with self.assertRaisesMessage(ValidationError, "before the hire date"):
            update_employee_details(user=self.admin, tenant=self.school_a, employee=employee, hire_date=date(1985, 1, 1))

    def test_campus_scoped_actor_cannot_update_a_different_campus_employee(self):
        employee = self.make_employee(campus=self.other_campus)
        with self.assertRaises(ValidationError):
            update_employee_details(user=self.scoped_admin, tenant=self.school_a, employee=employee, job_title="X")

    def test_campus_scoped_actor_can_update_own_campus_employee(self):
        employee = self.make_employee(campus=self.campus)
        updated = update_employee_details(user=self.scoped_admin, tenant=self.school_a, employee=employee, job_title="X")
        self.assertEqual(updated.job_title, "X")

    def test_changing_campus_rejects_when_linked_account_becomes_incompatible(self):
        employee = self.make_employee(campus=self.campus)
        scoped_teacher = User.objects.create_user(username="scoped-teacher", password="secret")
        teacher_role = Role.objects.create(tenant=self.school_a, name="Teacher", permissions=[])
        Membership.objects.create(tenant=self.school_a, user=scoped_teacher, role=teacher_role, campus=self.campus)
        link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=scoped_teacher)

        with self.assertRaises(ValidationError):
            update_employee_details(user=self.admin, tenant=self.school_a, employee=employee, campus=self.other_campus)

        membership = Membership.objects.get(tenant=self.school_a, user=scoped_teacher)
        self.assertEqual(membership.campus_id, self.campus.id)

    def test_changing_campus_is_allowed_when_linked_account_is_tenant_wide(self):
        employee = self.make_employee(campus=self.campus)
        link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=self.viewer)
        updated = update_employee_details(user=self.admin, tenant=self.school_a, employee=employee, campus=self.other_campus)
        self.assertEqual(updated.campus_id, self.other_campus.id)


class ChangeEmploymentStatusTests(StaffFoundationTests):
    def test_active_to_suspended_and_back(self):
        employee = self.make_employee()
        suspended = change_employment_status(user=self.admin, tenant=self.school_a, employee=employee, status=EmploymentStatus.SUSPENDED, reason="Investigation")
        self.assertEqual(suspended.status, EmploymentStatus.SUSPENDED)
        event = ActivityEvent.objects.get(action="staff.employee.suspended")
        self.assertEqual(event.metadata["reason"], "Investigation")

        reactivated = change_employment_status(user=self.admin, tenant=self.school_a, employee=suspended, status=EmploymentStatus.ACTIVE)
        self.assertEqual(reactivated.status, EmploymentStatus.ACTIVE)

    def test_terminated_is_terminal(self):
        employee = self.make_employee()
        terminated = change_employment_status(user=self.admin, tenant=self.school_a, employee=employee, status=EmploymentStatus.TERMINATED)
        with self.assertRaises(ValidationError):
            change_employment_status(user=self.admin, tenant=self.school_a, employee=terminated, status=EmploymentStatus.ACTIVE)

    def test_active_to_terminated_directly(self):
        employee = self.make_employee()
        terminated = change_employment_status(user=self.admin, tenant=self.school_a, employee=employee, status=EmploymentStatus.TERMINATED)
        self.assertEqual(terminated.status, EmploymentStatus.TERMINATED)

    def test_campus_scoped_actor_cannot_change_status_of_a_different_campus_employee(self):
        employee = self.make_employee(campus=self.other_campus)
        with self.assertRaises(ValidationError):
            change_employment_status(user=self.scoped_admin, tenant=self.school_a, employee=employee, status=EmploymentStatus.SUSPENDED)


class DocumentAndQualificationTests(TemporaryDocumentStorageMixin, StaffFoundationTests):
    def test_add_document(self):
        employee = self.make_employee()
        document = add_employee_document(
            user=self.admin, tenant=self.school_a, employee=employee, document_type="ID_COPY",
            file_obj=make_upload(name="id.pdf"), original_filename="id.pdf", content_type="application/pdf",
        )
        self.assertEqual(document.employee_id, employee.id)
        self.assertIsNotNone(document.document)
        self.assertEqual(document.document.original_filename, "id.pdf")
        self.assertTrue(ActivityEvent.objects.filter(action="staff.document_added").exists())

    def test_delete_document(self):
        employee = self.make_employee()
        document = add_employee_document(
            user=self.admin, tenant=self.school_a, employee=employee, document_type="ID_COPY",
            file_obj=make_upload(), original_filename="id.pdf", content_type="application/pdf",
        )
        delete_employee_document(user=self.admin, tenant=self.school_a, employee_document=document)
        self.assertFalse(employee.documents.filter(id=document.id).exists())
        self.assertTrue(ActivityEvent.objects.filter(action="staff.document_deleted").exists())

    def test_add_qualification(self):
        employee = self.make_employee()
        qualification = add_employee_qualification(
            user=self.admin, tenant=self.school_a, employee=employee, title="B.Ed Mathematics",
            institution="University", year_obtained=2015,
        )
        self.assertEqual(qualification.employee_id, employee.id)
        self.assertTrue(ActivityEvent.objects.filter(action="staff.qualification_added").exists())

    def test_qualification_year_out_of_range_is_rejected(self):
        employee = self.make_employee()
        with self.assertRaisesMessage(ValidationError, "year_obtained"):
            add_employee_qualification(user=self.admin, tenant=self.school_a, employee=employee, title="X", year_obtained=1800)

    def test_campus_scoped_actor_cannot_add_document_for_a_different_campus_employee(self):
        employee = self.make_employee(campus=self.other_campus)
        with self.assertRaises(ValidationError):
            add_employee_document(
                user=self.scoped_admin, tenant=self.school_a, employee=employee, document_type="ID_COPY",
                file_obj=make_upload(), original_filename="id.pdf", content_type="application/pdf",
            )

    def test_campus_scoped_actor_cannot_add_qualification_for_a_different_campus_employee(self):
        employee = self.make_employee(campus=self.other_campus)
        with self.assertRaises(ValidationError):
            add_employee_qualification(user=self.scoped_admin, tenant=self.school_a, employee=employee, title="X")


class UserLinkTests(StaffFoundationTests):
    def setUp(self):
        super().setUp()
        self.teacher_user = User.objects.create_user(username="teacher", password="secret")
        self.teacher_role = Role.objects.create(tenant=self.school_a, name="Teacher", permissions=[])
        Membership.objects.create(tenant=self.school_a, user=self.teacher_user, role=self.teacher_role)

    def test_link_requires_active_membership_in_tenant(self):
        employee = self.make_employee()
        stranger = User.objects.create_user(username="stranger", password="secret")
        with self.assertRaisesMessage(ValidationError, "active membership"):
            link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=stranger)

    def test_link_and_relink_same_user_is_idempotent(self):
        employee = self.make_employee()
        link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=self.teacher_user)
        count_before = ActivityEvent.objects.filter(action="staff.user_linked").count()
        relinked = link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=self.teacher_user)
        self.assertEqual(relinked.user_account_id, self.teacher_user.id)
        self.assertEqual(ActivityEvent.objects.filter(action="staff.user_linked").count(), count_before)

    def test_link_to_a_different_user_without_unlinking_first_is_rejected(self):
        employee = self.make_employee()
        link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=self.teacher_user)
        other_user = User.objects.create_user(username="other", password="secret")
        Membership.objects.create(tenant=self.school_a, user=other_user, role=self.teacher_role)
        with self.assertRaisesMessage(ValidationError, "unlink it first"):
            link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=other_user)

    def test_linking_a_user_already_linked_to_another_employee_is_rejected(self):
        employee_one = self.make_employee(employee_number="EMP-010")
        employee_two = self.make_employee(employee_number="EMP-011")
        link_user_account(user=self.admin, tenant=self.school_a, employee=employee_one, user_account=self.teacher_user)
        with self.assertRaisesMessage(ValidationError, "already linked"):
            link_user_account(user=self.admin, tenant=self.school_a, employee=employee_two, user_account=self.teacher_user)

    def test_unlink_then_relink_to_a_different_user_works(self):
        employee = self.make_employee()
        link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=self.teacher_user)
        unlink_user_account(user=self.admin, tenant=self.school_a, employee=employee)
        other_user = User.objects.create_user(username="other", password="secret")
        Membership.objects.create(tenant=self.school_a, user=other_user, role=self.teacher_role)
        relinked = link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=other_user)
        self.assertEqual(relinked.user_account_id, other_user.id)

    def test_unlink_when_already_unlinked_is_a_noop(self):
        employee = self.make_employee()
        count_before = ActivityEvent.objects.count()
        unlink_user_account(user=self.admin, tenant=self.school_a, employee=employee)
        self.assertEqual(ActivityEvent.objects.count(), count_before)

    def test_same_campus_membership_can_be_linked(self):
        employee = self.make_employee(campus=self.campus)
        scoped_teacher = User.objects.create_user(username="scoped-teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=scoped_teacher, role=self.teacher_role, campus=self.campus)
        linked = link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=scoped_teacher)
        self.assertEqual(linked.user_account_id, scoped_teacher.id)

    def test_different_campus_membership_cannot_be_linked(self):
        employee = self.make_employee(campus=self.campus)
        scoped_teacher = User.objects.create_user(username="scoped-teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=scoped_teacher, role=self.teacher_role, campus=self.other_campus)
        with self.assertRaises(ValidationError):
            link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=scoped_teacher)

    def test_tenant_wide_membership_can_be_linked_to_any_campus_employee(self):
        employee = self.make_employee(campus=self.campus)
        linked = link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=self.teacher_user)
        self.assertEqual(linked.user_account_id, self.teacher_user.id)

    def test_campus_scoped_membership_cannot_be_linked_to_an_unscoped_employee(self):
        employee = self.make_employee()
        scoped_teacher = User.objects.create_user(username="scoped-teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=scoped_teacher, role=self.teacher_role, campus=self.campus)
        with self.assertRaises(ValidationError):
            link_user_account(user=self.admin, tenant=self.school_a, employee=employee, user_account=scoped_teacher)

    def test_campus_scoped_actor_cannot_link_a_different_campus_employee(self):
        employee = self.make_employee(campus=self.other_campus)
        with self.assertRaises(ValidationError):
            link_user_account(user=self.scoped_admin, tenant=self.school_a, employee=employee, user_account=self.teacher_user)
