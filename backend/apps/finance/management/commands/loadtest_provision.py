import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, StudentEnrollment, Subject, Term
from apps.academics.services import enroll_student
from apps.assessments.models import AssessmentStatus, AssessmentType, MarkStatus
from apps.assessments.services import create_assessment, record_assessment_marks
from apps.attendance.models import AttendanceSessionStatus, AttendanceStatus
from apps.attendance.services import open_attendance_session, record_attendance_bulk, submit_attendance_session
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from ...models import FeeCategory, FeeItem, FeeStructure, InvoiceStatus, NumberSeries
from ...mpesa_services import configure_mpesa_gateway
from ...services import (
    add_fee_structure_line,
    approve_fee_structure,
    assign_fee_structure,
    create_fee_structure,
    generate_invoice,
    issue_invoice,
)

# Every finance.* permission string used anywhere in this app. Disposable
# load-test fixture data -- unlike a real school's role, there's no reason
# to scope this down.
ALL_FINANCE_PERMISSIONS = [
    "finance.allocation.reverse", "finance.credit_note.create",
    "finance.fee_structure.approve", "finance.fee_structure.create", "finance.fee_structure.edit", "finance.fee_structure.view",
    "finance.invoice.create", "finance.invoice.issue", "finance.invoice.view",
    "finance.mpesa.callback.process", "finance.mpesa.callback.verify", "finance.mpesa.callback.view",
    "finance.mpesa.configure",
    "finance.mpesa.stk_push.initiate", "finance.mpesa.stk_push.query", "finance.mpesa.stk_push.reconcile", "finance.mpesa.stk_push.view",
    "finance.payment.allocate", "finance.payment.record", "finance.payment.reverse", "finance.payment.view",
    "finance.reconciliation.ignore", "finance.reconciliation.ingest", "finance.reconciliation.match", "finance.reconciliation.view",
    "finance.setup.manage", "finance.setup.view",
    "finance.student_account.view",
]

# RC Area 6 / 5E-3: the interactive-read side of the mixed workload (student
# lists, attendance registers, assessment results) needs its own permissions
# on the same load-test operator account -- `any_class` on both domains
# means fixture provisioning doesn't need a TeacherAssignment per
# class/subject, same disposable-fixture philosophy as ALL_FINANCE_PERMISSIONS
# above. `.manage`/`.marks.manage`/`.session.manage`/`.override_calendar` are
# only needed to CREATE the one attendance session + one assessment below;
# `.record.view` is what the harness's read traffic actually exercises.
ACADEMIC_PERMISSIONS = [
    "students.view",
    "academics.students.enroll",
    "attendance.session.manage", "attendance.session.override_calendar", "attendance.any_class", "attendance.record.view",
    "assessment.manage", "assessment.marks.manage", "assessment.any_class", "assessment.record.view",
]

DEFAULT_PASSWORD = "loadtest-pass-not-for-production"
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[4] / "loadtest" / "manifest.json"
FEE_AMOUNT = Decimal("10000.00")


class Command(BaseCommand):
    help = "Provision tenants, students, invoices, and M-Pesa configuration for a Milestone 5E load-test run."

    def add_arguments(self, parser):
        parser.add_argument("--tenants", type=int, default=20, help="Number of tenants to provision.")
        parser.add_argument("--students-per-tenant", type=int, default=50)
        parser.add_argument("--noisy-multiplier", type=int, default=10,
                            help="Tenant 0 gets this many times the usual student pool, as the noisy neighbor.")
        parser.add_argument("--output", default=str(DEFAULT_MANIFEST_PATH))

    def handle(self, *args, **options):
        manifest = {"tenants": []}
        for index in range(options["tenants"]):
            is_noisy = index == 0
            student_count = options["students_per_tenant"] * (options["noisy_multiplier"] if is_noisy else 1)
            manifest["tenants"].append(self._provision_tenant(index=index, student_count=student_count, is_noisy=is_noisy))
            self.stdout.write(f"Provisioned {manifest['tenants'][-1]['slug']} ({student_count} students, noisy={is_noisy})")

        output_path = Path(options["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(manifest, indent=2))
        self.stdout.write(self.style.SUCCESS(f"Wrote manifest for {len(manifest['tenants'])} tenants to {output_path}"))

    @transaction.atomic
    def _provision_tenant(self, *, index, student_count, is_noisy):
        slug = f"loadtest-{index}"
        tenant, _ = Tenant.objects.get_or_create(slug=slug, defaults={"name": f"Load Test School {index}"})

        bursar, created = User.objects.get_or_create(username=f"loadtest-bursar-{index}")
        if created:
            bursar.set_password(DEFAULT_PASSWORD)
            bursar.save(update_fields=["password"])
        all_permissions = sorted(set(ALL_FINANCE_PERMISSIONS) | set(ACADEMIC_PERMISSIONS))
        role, _ = Role.objects.get_or_create(tenant=tenant, name="Load Test Bursar", defaults={"permissions": all_permissions})
        if role.permissions != all_permissions:
            role.permissions = all_permissions
            role.save(update_fields=["permissions"])
        Membership.objects.get_or_create(tenant=tenant, user=bursar, role=role)

        NumberSeries.objects.get_or_create(tenant=tenant, document_type="RECEIPT", defaults={"prefix": f"LT{index}-RCT-", "padding": 6})
        NumberSeries.objects.get_or_create(tenant=tenant, document_type="INVOICE", defaults={"prefix": f"LT{index}-INV-", "padding": 6})

        config = configure_mpesa_gateway(
            user=bursar, tenant=tenant, environment="SANDBOX", shortcode="600000",
            consumer_key=f"loadtest-key-{index}", consumer_secret=f"loadtest-secret-{index}", passkey=f"loadtest-passkey-{index}",
        )

        year, _ = AcademicYear.objects.get_or_create(
            tenant=tenant, name="Load Test Year", defaults={"starts_on": date(2026, 1, 1), "ends_on": date(2026, 12, 31)},
        )
        level, _ = AcademicLevel.objects.get_or_create(tenant=tenant, code="LT", defaults={"name": "Load Test Level", "sequence": 1})
        category, _ = FeeCategory.objects.get_or_create(tenant=tenant, code="LT", defaults={"name": "Load Test Fees"})
        item, _ = FeeItem.objects.get_or_create(tenant=tenant, category=category, code="TUITION", defaults={"name": "Tuition"})

        structure = _get_or_create_fee_structure(user=bursar, tenant=tenant, year=year, level=level, item=item)

        students = []
        student_objects = []
        for n in range(student_count):
            admission_number = f"LT{index}-{n:05d}"
            student, _ = Student.objects.get_or_create(
                tenant=tenant, admission_number=admission_number, defaults={"first_name": "Load", "last_name": f"Test{n}"},
            )
            assignment = assign_fee_structure(user=bursar, tenant=tenant, student=student, fee_structure=structure)
            invoice = generate_invoice(user=bursar, tenant=tenant, assignment=assignment)
            if invoice.status == InvoiceStatus.DRAFT:
                issue_invoice(user=bursar, tenant=tenant, invoice=invoice)
                invoice.refresh_from_db()
            students.append({"admission_number": admission_number, "student_id": str(student.id), "invoice_id": str(invoice.id)})
            student_objects.append(student)

        attendance_session_id, assessment_id = self._provision_academic_fixtures(
            bursar=bursar, tenant=tenant, year=year, level=level, students=student_objects,
        )

        return {
            "slug": slug, "is_noisy": is_noisy, "callback_token": config.callback_token,
            "bursar_username": bursar.username, "bursar_password": DEFAULT_PASSWORD,
            "students": students,
            "attendance_session_id": attendance_session_id, "assessment_id": assessment_id,
        }

    def _provision_academic_fixtures(self, *, bursar, tenant, year, level, students):
        """RC Area 6 / 5E-3: real Student/Attendance/Assessment read-path
        traffic needs real data to read, not empty lists -- this closes the
        mixed-workload fixture gap the same way the rest of this command
        already covers Finance. One campus/class/subject/term/session/
        assessment per tenant, every already-provisioned student enrolled
        and given a real attendance + assessment record, via the same
        services.py functions the real API views call (not raw ORM writes
        for the business objects), mirroring this file's existing pattern.
        """
        campus, _ = Campus.objects.get_or_create(tenant=tenant, code="LT", defaults={"name": "Load Test Campus"})
        class_group, _ = ClassGroup.objects.get_or_create(
            tenant=tenant, code="LT-C1", defaults={"name": "Load Test Class", "academic_level": level, "campus": campus},
        )
        subject, _ = Subject.objects.get_or_create(tenant=tenant, code="LTSUB", defaults={"name": "Load Test Subject"})
        term, _ = Term.objects.get_or_create(
            tenant=tenant, academic_year=year, sequence=1,
            defaults={"name": "Load Test Term", "starts_on": year.starts_on, "ends_on": year.ends_on},
        )
        assessment_type, _ = AssessmentType.objects.get_or_create(tenant=tenant, code="LTAT", defaults={"name": "Load Test CAT"})

        already_enrolled = set(StudentEnrollment.objects.filter(
            tenant=tenant, academic_year=year, student__in=students,
        ).values_list("student_id", flat=True))
        for student in students:
            if student.id in already_enrolled:
                continue
            enroll_student(
                user=bursar, tenant=tenant, student=student, academic_year=year,
                academic_level=level, class_group=class_group, campus=campus, term=term,
            )

        # A fixed date (not date.today()), so re-running this command on a
        # different day still resolves to the same, already-open session
        # (open_attendance_session is idempotent per (tenant, class_group,
        # session_date)) instead of accumulating a new one every re-run.
        # force=True since this fixed date may not be an instructional day
        # per AttendanceSetup's default Mon-Fri calendar -- the read traffic
        # this feeds only needs a real, submitted session to exist, not a
        # calendar-accurate one.
        session_date = date(2026, 6, 1)
        session, _records = open_attendance_session(user=bursar, tenant=tenant, class_group=class_group, session_date=session_date, force=True)
        if session.status == AttendanceSessionStatus.OPEN:
            record_attendance_bulk(
                user=bursar, tenant=tenant, session=session,
                entries=[{"student": student, "status": AttendanceStatus.PRESENT, "remarks": ""} for student in students],
            )
            submit_attendance_session(user=bursar, tenant=tenant, session=session)

        assessment, _results = create_assessment(
            user=bursar, tenant=tenant, term=term, class_group=class_group, subject=subject,
            assessment_type=assessment_type, name="Load Test CAT 1", max_marks=100, scheduled_date=session_date,
        )
        if assessment.status == AssessmentStatus.DRAFT:
            record_assessment_marks(
                user=bursar, tenant=tenant, assessment=assessment,
                entries=[{"student": student, "mark_status": MarkStatus.SCORED, "score": Decimal("70"), "remarks": ""} for student in students],
            )

        return str(session.id), str(assessment.id)


def _get_or_create_fee_structure(*, user, tenant, year, level, item):
    """One shared, approved fee structure per tenant -- created on first
    provisioning of that tenant, reused on every re-run (loadtest_provision
    is idempotent, mirroring configure_mpesa_gateway's own idempotency).
    """
    structure = FeeStructure.objects.filter(tenant=tenant, academic_year=year, academic_level=level).first()
    if structure is None:
        structure = create_fee_structure(user=user, tenant=tenant, name="Load Test Structure", academic_year=year, academic_level=level)
    if not structure.lines.exists():
        add_fee_structure_line(user=user, tenant=tenant, fee_structure=structure, fee_item=item, amount=FEE_AMOUNT)
    if not structure.is_approved:
        approve_fee_structure(user=user, tenant=tenant, fee_structure=structure)
    return structure
