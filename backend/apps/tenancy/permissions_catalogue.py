"""A reference catalogue of every permission string this codebase actually
checks today (via apps.tenancy.services.require_permission), plus the four
new codes this milestone introduces for tenant user/role administration
itself. This is NOT new enforcement -- every existing require_permission
call site is unchanged; this just gives a role-editor UI something to
enumerate instead of freeform text entry, mirroring
apps.platform.catalogue.MODULE_CATALOGUE's established pattern.

Grouping for a frontend (e.g. "finance", "leave") is derived from
`code.split(".")[0]` at the API layer -- not stored here.
"""

PERMISSION_CATALOGUE = {
    # Tenant user & role administration (this milestone)
    "tenancy.membership.view": "View tenant members",
    "tenancy.membership.manage": "Invite, activate/deactivate, and reassign tenant members",
    "tenancy.role.view": "View roles and permissions",
    "tenancy.role.manage": "Create, edit, and delete roles",

    # Admissions
    "admissions.document.manage": "Upload and delete application documents",
    "admissions.enroll": "Enroll an accepted application into a Student with an academic placement",

    # Students
    "students.view": "View students",
    "students.document.view": "View student documents",
    "students.document.manage": "Upload and delete student documents",

    # Academics / assessments
    "academics.students.enroll": "Place a student into a class/section for an academic year",
    "assessment.manage": "Open, mark, submit, and manage assessments",
    "assessment.record.view": "View assessment records and results",
    "assessment.setup.view": "View grading schemes",
    "assessment.setup.manage": "Manage grading schemes",

    # Attendance
    "attendance.session.manage": "Open, mark, and submit attendance sessions",
    "attendance.record.view": "View attendance records and summaries",

    # Timetable
    "timetable.manage": "Create and edit timetable entries",
    "timetable.record.view": "View timetables",
    "timetable.setup.view": "View timetable setup (periods)",
    "timetable.setup.manage": "Manage timetable setup (periods)",

    # Staff / HR
    "staff.view": "View employee records",
    "staff.manage": "Create and edit employee records",
    "staff.user_link.manage": "Link/unlink an employee to a user account",

    # Leave
    "leave.request.view": "View leave requests",
    "leave.request.manage": "Submit leave requests",
    "leave.approve": "Approve or reject leave requests",
    "leave.balance.adjust": "Adjust employee leave balances",
    "leave.setup.view": "View leave types and workflows",
    "leave.setup.manage": "Manage leave types and workflows",

    # Finance
    "finance.setup.view": "View finance setup",
    "finance.setup.manage": "Manage finance setup",
    "finance.fee_structure.view": "View fee structures",
    "finance.fee_structure.create": "Create fee structures",
    "finance.fee_structure.edit": "Edit fee structures",
    "finance.fee_structure.approve": "Approve fee structures",
    "finance.invoice.view": "View invoices",
    "finance.invoice.create": "Generate invoices",
    "finance.invoice.issue": "Issue invoices",
    "finance.credit_note.create": "Create credit notes",
    "finance.student_account.view": "View a student's finance summary",
    "finance.payment.view": "View payments",
    "finance.payment.record": "Record payments",
    "finance.payment.allocate": "Allocate payments to invoices",
    "finance.payment.reverse": "Reverse payments",
    "finance.allocation.reverse": "Reverse a payment allocation",
    "finance.reconciliation.view": "View M-Pesa reconciliation",
    "finance.reconciliation.ingest": "Ingest incoming M-Pesa payments",
    "finance.reconciliation.match": "Match incoming M-Pesa payments",
    "finance.reconciliation.ignore": "Ignore incoming M-Pesa payments",
    "finance.mpesa.configure": "Configure the M-Pesa gateway",
    "finance.mpesa.callback.view": "View M-Pesa callback logs",
    "finance.mpesa.callback.verify": "Verify M-Pesa callbacks",
    "finance.mpesa.callback.process": "Process M-Pesa callbacks",
    "finance.mpesa.stk_push.view": "View STK push requests",
    "finance.mpesa.stk_push.initiate": "Initiate an STK push",
    "finance.mpesa.stk_push.query": "Query an STK push status",
    "finance.mpesa.stk_push.reconcile": "Reconcile a completed STK push",

    # Documents
    "documents.setup.view": "View document setup",
    "documents.setup.manage": "Manage document setup",

    # Notifications
    "notifications.record.view": "View sent notifications",
    "notifications.retry": "Retry a failed notification",
    "notifications.setup.view": "View notification setup",
    "notifications.setup.manage": "Manage notification setup",
    "notifications.templates.view": "View notification templates",
    "notifications.templates.manage": "Manage notification templates",
    "notifications.rules.view": "View notification rules",
    "notifications.rules.manage": "Manage notification rules",

    # Reporting (dynamically composed by apps.reporting.api as
    # f"reports.{permission_group}.{view|export}" -- enumerated here from
    # the permission_group values in apps.reporting.catalogue)
    "reports.students.view": "View student reports",
    "reports.students.export": "Export student reports",
    "reports.attendance.view": "View attendance reports",
    "reports.attendance.export": "Export attendance reports",
    "reports.assessments.view": "View assessment reports",
    "reports.assessments.export": "Export assessment reports",
    "reports.finance.view": "View finance reports",
    "reports.finance.export": "Export finance reports",
    "reports.staff.view": "View staff reports",
    "reports.staff.export": "Export staff reports",
}


def validate_permission_codes(codes):
    """Canonicalizes to a sorted, deduplicated list and rejects anything not
    in PERMISSION_CATALOGUE -- mirrors apps.platform.services._validate_module_codes.
    """
    from django.core.exceptions import ValidationError

    canonical = sorted(set(codes))
    unknown = set(canonical) - set(PERMISSION_CATALOGUE)
    if unknown:
        raise ValidationError(f"Unknown permission code(s): {', '.join(sorted(unknown))}")
    return canonical
