from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.academics.models import (
    AcademicLevel, AcademicYear, ClassGroup, EnrollmentStatus, StudentEnrollment, Subject, TeacherAssignment, Term,
)
from apps.finance.models import NumberSeries, PaymentMethod
from apps.leave.models import LeaveApprovalWorkflow, LeaveApprovalWorkflowStage, LeaveType
from apps.platform.catalogue import MODULE_CATALOGUE
from apps.platform.models import SubscriptionPlan, TenantSubscription
from apps.staff.models import Employee, EmploymentType
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User
from apps.tenancy.permissions_catalogue import PERMISSION_CATALOGUE

# One shared, cross-tenant account -- not scoped per school, since a real
# Super Admin is inherently cross-tenant (apps.platform's IsSuperUser).
# Re-running this command for additional schools just adds another
# membership to the same account, so one login can provision/browse all
# of them.
SUPERADMIN_USERNAME = "demo-superadmin"
DEMO_PASSWORD = "demo-pass-12345"

# Every finance.* permission this app defines, plus the minimal read-access
# outside finance a bursar actually needs (looking up which student an
# invoice belongs to, exporting finance reports). Mirrors
# apps/finance/management/commands/loadtest_provision.py's ALL_FINANCE_PERMISSIONS,
# kept separate/inlined here since that module is load-test-only tooling,
# not meant to be imported from.
BURSAR_PERMISSIONS = [
    "students.view",
    # Needed to populate the fee-structure-creation picker (academic years,
    # levels, terms) -- without it every one of those catalogue endpoints
    # 403s for a bursar, silently leaving the "New fee structure" dialog's
    # pickers empty even though finance.fee_structure.create is granted.
    "academics.setup.view",
    "finance.setup.view", "finance.setup.manage",
    "finance.fee_structure.view", "finance.fee_structure.create", "finance.fee_structure.edit", "finance.fee_structure.approve",
    "finance.invoice.view", "finance.invoice.create", "finance.invoice.issue",
    "finance.credit_note.create",
    "finance.student_account.view",
    "finance.payment.view", "finance.payment.record", "finance.payment.allocate", "finance.payment.reverse",
    "finance.allocation.reverse",
    "finance.reconciliation.view", "finance.reconciliation.ingest", "finance.reconciliation.match", "finance.reconciliation.ignore",
    "finance.mpesa.configure",
    "finance.mpesa.callback.view", "finance.mpesa.callback.verify", "finance.mpesa.callback.process",
    "finance.mpesa.stk_push.view", "finance.mpesa.stk_push.initiate", "finance.mpesa.stk_push.query", "finance.mpesa.stk_push.reconcile",
    "reports.finance.view", "reports.finance.export",
]

STUDENT_NAMES = [
    ("John", "Kamau"), ("Mary", "Wanjiku"), ("Peter", "Otieno"), ("Grace", "Achieng"),
    ("Samuel", "Njoroge"), ("Faith", "Atieno"), ("Daniel", "Mwangi"), ("Esther", "Wairimu"),
    ("Brian", "Kiptoo"), ("Lilian", "Cherono"), ("Kevin", "Omondi"), ("Ann", "Nyambura"),
    ("Joseph", "Kariuki"), ("Purity", "Wangui"), ("Moses", "Odhiambo"), ("Cynthia", "Chebet"),
]


class Command(BaseCommand):
    help = "Idempotently seeds one demo tenant with enough real data (students, class groups, a leave workflow) to log in and exercise the migrated frontend pages locally. Run multiple times with different --slug/--name to get several independent demo schools -- each gets its own admin/bursar/teacher accounts, scoped by slug, so schools don't share logins or see each other's data. Dev/test tooling only -- not for a real deployment."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="demo-academy")
        parser.add_argument("--name", default="Demo Academy")

    def handle(self, *args, **options):
        slug = options["slug"]
        tenant = self._provision_tenant(slug=slug, name=options["name"])
        campus = self._provision_campus(tenant)
        admin_role = self._provision_admin_role(tenant)
        admin_user = self._provision_user(tenant=tenant, role=admin_role, campus=None, username=f"{slug}-admin", first_name="Admin")
        # Superuser bypasses every permission-string check (require_permission
        # etc.) regardless of which role its membership carries -- reusing
        # admin_role here is just so the account also has something sensible
        # to browse inside this school; apps.platform's IsSuperUser (the
        # Super Admin tenant-provisioning API) needs no membership at all.
        # Unlike the other three accounts, this one is NOT slug-scoped -- see
        # SUPERADMIN_USERNAME above.
        superadmin_user = self._provision_user(
            tenant=tenant, role=admin_role, campus=None, username=SUPERADMIN_USERNAME, first_name="Super Admin", is_superuser=True,
        )
        teacher_role = self._provision_teacher_role(tenant)
        teacher_user = self._provision_user(tenant=tenant, role=teacher_role, campus=campus, username=f"{slug}-teacher", first_name="Teacher")
        bursar_role = self._provision_bursar_role(tenant)
        bursar_user = self._provision_user(tenant=tenant, role=bursar_role, campus=None, username=f"{slug}-bursar", first_name="Bursar")

        year = self._provision_academic_year(tenant)
        self._provision_terms(tenant, year)
        levels = self._provision_levels(tenant)
        subject = self._provision_subject(tenant)
        class_groups = self._provision_class_groups(tenant, campus, levels)
        TeacherAssignment.objects.get_or_create(tenant=tenant, teacher=teacher_user, class_group=class_groups[0], subject=subject)

        self._provision_students(tenant=tenant, campus=campus, year=year, levels=levels, class_groups=class_groups)
        self._provision_staff(tenant=tenant, campus=campus, teacher_user=teacher_user)
        self._provision_leave_workflow(tenant)
        self._provision_finance_number_series(tenant)

        self.stdout.write(self.style.SUCCESS(f"\nDemo tenant ready: {tenant.name} ({tenant.slug})"))
        self.stdout.write("Log in with any account (tenant is selected automatically after login):")
        self.stdout.write(f"  Super Admin (cross-tenant, shared across every demo school) -- username: {superadmin_user.username}  password: {DEMO_PASSWORD}")
        self.stdout.write(f"  School Administrator (full access, this school only) -- username: {admin_user.username}  password: {DEMO_PASSWORD}")
        self.stdout.write(f"  Bursar (finance-scoped, this school only) -- username: {bursar_user.username}  password: {DEMO_PASSWORD}")
        self.stdout.write(f"  Teacher (class-scoped, this school only) -- username: {teacher_user.username}  password: {DEMO_PASSWORD}")

    @transaction.atomic
    def _provision_tenant(self, *, slug, name):
        tenant, _ = Tenant.objects.get_or_create(slug=slug, defaults={"name": name})
        plan, _ = SubscriptionPlan.objects.get_or_create(
            name="Demo Plan -- All Modules", defaults={"module_codes": sorted(MODULE_CATALOGUE), "is_active": True},
        )
        if sorted(plan.module_codes) != sorted(MODULE_CATALOGUE):
            plan.module_codes = sorted(MODULE_CATALOGUE)
            plan.save(update_fields=["module_codes"])
        subscription, created = TenantSubscription.objects.get_or_create(tenant=tenant, defaults={"plan": plan})
        if not created and subscription.plan_id != plan.id:
            subscription.plan = plan
            subscription.save(update_fields=["plan"])
        return tenant

    def _provision_campus(self, tenant):
        campus, _ = Campus.objects.get_or_create(tenant=tenant, code="MAIN", defaults={"name": "Main Campus"})
        return campus

    def _provision_admin_role(self, tenant):
        permissions = sorted(PERMISSION_CATALOGUE)
        role, _ = Role.objects.get_or_create(tenant=tenant, name="Demo Administrator", defaults={"permissions": permissions})
        if role.permissions != permissions:
            role.permissions = permissions
            role.save(update_fields=["permissions"])
        return role

    def _provision_teacher_role(self, tenant):
        permissions = ["attendance.session.manage", "attendance.record.view", "leave.request.view", "leave.request.manage", "students.view"]
        role, _ = Role.objects.get_or_create(tenant=tenant, name="Demo Teacher", defaults={"permissions": permissions})
        if role.permissions != permissions:
            role.permissions = permissions
            role.save(update_fields=["permissions"])
        return role

    def _provision_bursar_role(self, tenant):
        role, _ = Role.objects.get_or_create(tenant=tenant, name="Demo Bursar", defaults={"permissions": BURSAR_PERMISSIONS})
        if role.permissions != BURSAR_PERMISSIONS:
            role.permissions = BURSAR_PERMISSIONS
            role.save(update_fields=["permissions"])
        return role

    def _provision_user(self, *, tenant, role, campus, username, first_name, is_superuser=False):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first_name, "is_superuser": is_superuser, "is_staff": is_superuser},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
        elif user.is_superuser != is_superuser:
            user.is_superuser = is_superuser
            user.is_staff = is_superuser
            user.save(update_fields=["is_superuser", "is_staff"])
        Membership.objects.get_or_create(tenant=tenant, user=user, defaults={"role": role, "campus": campus})
        return user

    def _provision_academic_year(self, tenant):
        today = timezone.now().date()
        year, _ = AcademicYear.objects.get_or_create(
            tenant=tenant, name="Current", defaults={"starts_on": today - timedelta(days=180), "ends_on": today + timedelta(days=180), "is_current": True},
        )
        return year

    def _provision_terms(self, tenant, year):
        # Three terms spanning the academic year -- gives Finance's
        # fee-structure-creation UI (term-scoped) real data to pick from,
        # and different terms a real chance to carry different fee items.
        span_days = (year.ends_on - year.starts_on).days
        third = span_days // 3
        bounds = [
            (year.starts_on, year.starts_on + timedelta(days=third)),
            (year.starts_on + timedelta(days=third + 1), year.starts_on + timedelta(days=2 * third)),
            (year.starts_on + timedelta(days=2 * third + 1), year.ends_on),
        ]
        return [
            Term.objects.get_or_create(
                tenant=tenant, academic_year=year, sequence=sequence,
                defaults={"name": f"Term {sequence}", "starts_on": starts_on, "ends_on": ends_on},
            )[0]
            for sequence, (starts_on, ends_on) in enumerate(bounds, start=1)
        ]

    def _provision_levels(self, tenant):
        return [
            AcademicLevel.objects.get_or_create(tenant=tenant, code=code, defaults={"name": name, "sequence": sequence})[0]
            for sequence, (code, name) in enumerate([("G7", "Grade 7"), ("G8", "Grade 8")], start=1)
        ]

    def _provision_subject(self, tenant):
        subject, _ = Subject.objects.get_or_create(tenant=tenant, code="MATH", defaults={"name": "Mathematics"})
        return subject

    def _provision_class_groups(self, tenant, campus, levels):
        specs = [("G7-E", "Grade 7 East", levels[0]), ("G8-E", "Grade 8 East", levels[1])]
        return [
            ClassGroup.objects.get_or_create(tenant=tenant, code=code, defaults={"name": name, "academic_level": level, "campus": campus})[0]
            for code, name, level in specs
        ]

    def _provision_students(self, *, tenant, campus, year, levels, class_groups):
        for index, (first_name, last_name) in enumerate(STUDENT_NAMES):
            class_group = class_groups[index % len(class_groups)]
            level = levels[index % len(levels)]
            student, _ = Student.objects.get_or_create(
                tenant=tenant, admission_number=f"ADM{index + 1:03d}", defaults={"first_name": first_name, "last_name": last_name, "campus": campus},
            )
            StudentEnrollment.objects.get_or_create(
                tenant=tenant, student=student, academic_year=year,
                defaults={"academic_level": level, "class_group": class_group, "campus": campus, "status": EnrollmentStatus.ACTIVE},
            )

    def _provision_staff(self, *, tenant, campus, teacher_user):
        Employee.objects.get_or_create(
            tenant=tenant, employee_number="EMP001",
            defaults={
                "first_name": "Demo", "last_name": "Teacher", "campus": campus, "job_title": "Class Teacher",
                "employment_type": EmploymentType.PERMANENT, "hire_date": date.today() - timedelta(days=365),
                "user_account": teacher_user,
            },
        )
        Employee.objects.get_or_create(
            tenant=tenant, employee_number="EMP002",
            defaults={
                "first_name": "Grace", "last_name": "Mutiso", "campus": campus, "job_title": "Administrator",
                "employment_type": EmploymentType.PERMANENT, "hire_date": date.today() - timedelta(days=720),
            },
        )

    def _provision_finance_number_series(self, tenant):
        # generate_invoice/issue_credit_note/record_payment/reverse_payment all
        # hard-require a NumberSeries row per document type (services.py's
        # _next_number raises ValidationError otherwise) -- mirrors the two
        # series loadtest_provision.py already sets up, extended to all four
        # document types Finance actually issues.
        for document_type, prefix in [("INVOICE", "INV-"), ("CREDIT_NOTE", "CRN-"), ("RECEIPT", "RCT-"), ("PAYMENT_REVERSAL", "REV-")]:
            NumberSeries.objects.get_or_create(tenant=tenant, document_type=document_type, defaults={"prefix": prefix, "padding": 6})
        # PaymentMethodListView is deliberately read-only (api.py:182-192) --
        # rows only ever come from M-Pesa auto-provisioning or "directly via
        # shell/tests". This is that direct-creation path for the demo tenant,
        # so the Record Payment dialog has something to select.
        PaymentMethod.objects.get_or_create(tenant=tenant, code="CASH", defaults={"name": "Cash"})

    def _provision_leave_workflow(self, tenant):
        line_manager_role, _ = Role.objects.get_or_create(tenant=tenant, name="Line Manager", defaults={"permissions": ["leave.approve"]})
        hr_role, _ = Role.objects.get_or_create(tenant=tenant, name="HR", defaults={"permissions": ["leave.approve", "leave.balance.adjust"]})
        workflow, _ = LeaveApprovalWorkflow.objects.get_or_create(tenant=tenant, name="Standard Leave Approval")
        LeaveApprovalWorkflowStage.objects.get_or_create(
            tenant=tenant, workflow=workflow, sequence=1, defaults={"name": "Line Manager approval", "approver_role": line_manager_role},
        )
        LeaveApprovalWorkflowStage.objects.get_or_create(
            tenant=tenant, workflow=workflow, sequence=2, defaults={"name": "HR approval", "approver_role": hr_role},
        )
        LeaveType.objects.get_or_create(
            tenant=tenant, code="ANNUAL",
            defaults={"name": "Annual Leave", "default_annual_entitlement_days": 21, "approval_workflow": workflow},
        )
