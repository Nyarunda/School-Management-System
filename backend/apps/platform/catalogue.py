from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleDefinition:
    label: str
    apps: tuple


# Fixed platform code, not a DB table -- module *definitions* (what exists,
# which Django apps a module covers) can never be tenant-supplied, exactly
# like apps.reporting.catalogue.REPORT_CATALOGUE. Which modules a tenant's
# subscription actually includes is the only thing that's data (see
# services.get_enabled_modules). `admissions` gates its one endpoint
# (apps.admissions.api.ApplicationEnrollView) via require_module_enabled;
# `academics` (via timetable) and `guardians` (bundled under
# student_records) still have no api.py of their own -- included here for
# completeness/future-proofing even though there's no resolver to wire yet.
# `tenancy` and `activity` are core platform infrastructure and are never
# gated.
MODULE_CATALOGUE = {
    "admissions": ModuleDefinition(label="Admissions", apps=("admissions",)),
    "student_records": ModuleDefinition(label="Student Records", apps=("students", "guardians")),
    "academics": ModuleDefinition(label="Academics & Timetable", apps=("academics", "timetable")),
    "assessments": ModuleDefinition(label="Assessments & Grading", apps=("assessments",)),
    "attendance": ModuleDefinition(label="Attendance", apps=("attendance",)),
    "staff_hr": ModuleDefinition(label="Staff & HR", apps=("staff", "leave")),
    "finance": ModuleDefinition(label="Finance & Payments", apps=("finance",)),
    "documents": ModuleDefinition(label="Documents", apps=("documents",)),
    "communications": ModuleDefinition(label="Communications & Notifications", apps=("notifications",)),
}
