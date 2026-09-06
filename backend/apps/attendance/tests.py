from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, EnrollmentStatus, StudentEnrollment, Subject, TeacherAssignment
from apps.activity.models import ActivityEvent
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import AttendanceRecord, AttendanceSession, AttendanceSetup, AttendanceStatus
from .services import open_attendance_session, record_attendance_bulk


class AttendanceFoundationTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="Admin",
            permissions=["attendance.session.manage", "attendance.record.view", "attendance.any_class"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.admin_role)

        self.teacher = User.objects.create_user(username="teacher", password="secret")
        self.teacher_role = Role.objects.create(
            tenant=self.school_a, name="Teacher", permissions=["attendance.session.manage", "attendance.record.view"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.teacher_role)

        self.other_teacher = User.objects.create_user(username="other-teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.other_teacher, role=self.teacher_role)

        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.year = AcademicYear.objects.create(
            tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31), is_current=True,
        )
        self.level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_group = ClassGroup.objects.create(
            tenant=self.school_a, name="Grade 8 East", code="G8-E", academic_level=self.level, campus=self.campus,
        )
        self.subject = Subject.objects.create(tenant=self.school_a, name="Math", code="MATH")
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.class_group, subject=self.subject)

        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        StudentEnrollment.objects.create(
            tenant=self.school_a, student=self.student, academic_year=self.year, academic_level=self.level,
            class_group=self.class_group, campus=self.campus, status=EnrollmentStatus.ACTIVE,
        )
        self.other_student = Student.objects.create(tenant=self.school_a, admission_number="ADM-002", first_name="Brian", last_name="Kariuki")
        StudentEnrollment.objects.create(
            tenant=self.school_a, student=self.other_student, academic_year=self.year, academic_level=self.level,
            class_group=self.class_group, campus=self.campus, status=EnrollmentStatus.ACTIVE,
        )
        self.session_date = date(2026, 3, 2)  # a Monday

    def open_session(self, **overrides):
        values = dict(user=self.teacher, tenant=self.school_a, class_group=self.class_group, session_date=self.session_date)
        values.update(overrides)
        return open_attendance_session(**values)


class RosterResolutionTests(AttendanceFoundationTests):
    def test_roster_resolves_only_active_enrollments_for_the_covering_academic_year(self):
        other_year = AcademicYear.objects.create(tenant=self.school_a, name="2025", starts_on=date(2025, 1, 1), ends_on=date(2025, 12, 31))
        transferred = Student.objects.create(tenant=self.school_a, admission_number="ADM-003", first_name="Left", last_name="School")
        StudentEnrollment.objects.create(
            tenant=self.school_a, student=transferred, academic_year=self.year, academic_level=self.level,
            class_group=self.class_group, campus=self.campus, status=EnrollmentStatus.TRANSFERRED,
        )
        not_this_year = Student.objects.create(tenant=self.school_a, admission_number="ADM-004", first_name="Old", last_name="Year")
        StudentEnrollment.objects.create(
            tenant=self.school_a, student=not_this_year, academic_year=other_year, academic_level=self.level,
            class_group=self.class_group, campus=self.campus, status=EnrollmentStatus.ACTIVE,
        )

        session, roster = self.open_session()

        roster_student_ids = {enrollment.student_id for enrollment in roster}
        self.assertEqual(roster_student_ids, {self.student.id, self.other_student.id})

    def test_no_covering_academic_year_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "No academic year covers this date"):
            self.open_session(session_date=date(2030, 1, 1))


class SessionOpenTests(AttendanceFoundationTests):
    def test_open_is_idempotent(self):
        first, _ = self.open_session()
        second, _ = self.open_session()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AttendanceSession.objects.count(), 1)

    def test_non_instructional_day_is_rejected_without_force(self):
        saturday = date(2026, 3, 7)
        with self.assertRaisesMessage(ValidationError, "not an instructional day"):
            self.open_session(session_date=saturday)
        self.assertEqual(AttendanceSession.objects.count(), 0)

    def test_non_instructional_day_can_be_forced(self):
        saturday = date(2026, 3, 7)
        session, _ = self.open_session(session_date=saturday, force=True)
        self.assertEqual(session.session_date, saturday)

    def test_custom_instructional_days_are_respected(self):
        AttendanceSetup.objects.create(tenant=self.school_a, instructional_days=[6, 7])  # weekends only
        with self.assertRaisesMessage(ValidationError, "not an instructional day"):
            self.open_session()  # session_date is a Monday
        saturday = date(2026, 3, 7)
        session, _ = self.open_session(session_date=saturday)
        self.assertEqual(session.session_date, saturday)


class AuthorizationTests(AttendanceFoundationTests):
    def test_assigned_teacher_can_open_a_session(self):
        session, _ = self.open_session(user=self.teacher)
        self.assertIsNotNone(session)

    def test_unassigned_teacher_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "not assigned to this class"):
            self.open_session(user=self.other_teacher)
        self.assertEqual(AttendanceSession.objects.count(), 0)

    def test_any_class_permission_bypasses_the_assignment_check(self):
        session, _ = self.open_session(user=self.admin)
        self.assertIsNotNone(session)


class RecordAttendanceBulkTests(AttendanceFoundationTests):
    def test_creates_records_for_valid_roster_entries(self):
        session, _ = self.open_session()
        records = record_attendance_bulk(
            user=self.teacher, tenant=self.school_a, session=session,
            entries=[
                {"student": self.student, "status": AttendanceStatus.PRESENT},
                {"student": self.other_student, "status": AttendanceStatus.ABSENT, "remarks": "Sick note pending"},
            ],
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(AttendanceRecord.objects.filter(session=session).count(), 2)

    def test_student_not_on_the_roster_is_rejected(self):
        session, _ = self.open_session()
        outsider = Student.objects.create(tenant=self.school_a, admission_number="ADM-999", first_name="Not", last_name="Enrolled")
        with self.assertRaisesMessage(ValidationError, "is not enrolled in this class"):
            record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                                   entries=[{"student": outsider, "status": AttendanceStatus.PRESENT}])
        self.assertEqual(AttendanceRecord.objects.count(), 0)

    def test_status_correction_updates_the_record_and_logs_activity(self):
        session, _ = self.open_session()
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.ABSENT}])
        self.assertEqual(ActivityEvent.objects.count(), 0)  # first-time creation is not a "correction"

        record_attendance_bulk(user=self.admin, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.PRESENT, "remarks": "Arrived late, marked present"}])

        record = AttendanceRecord.objects.get(session=session, student=self.student)
        self.assertEqual(record.status, AttendanceStatus.PRESENT)
        self.assertEqual(ActivityEvent.objects.count(), 1)
        event = ActivityEvent.objects.get()
        self.assertEqual(event.action, "attendance.record.corrected")
        self.assertEqual(event.metadata, {"previous_status": AttendanceStatus.ABSENT, "new_status": AttendanceStatus.PRESENT})

    def test_resubmitting_the_same_status_is_a_no_op_for_the_audit_log(self):
        session, _ = self.open_session()
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.PRESENT}])
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.PRESENT}])
        self.assertEqual(ActivityEvent.objects.count(), 0)

    def test_cross_tenant_session_is_rejected(self):
        foreign_campus = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        foreign_year = AcademicYear.objects.create(tenant=self.school_b, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        foreign_level = AcademicLevel.objects.create(tenant=self.school_b, name="Grade 8", code="G8", sequence=8)
        foreign_class = ClassGroup.objects.create(tenant=self.school_b, name="Grade 8", code="G8", academic_level=foreign_level, campus=foreign_campus)
        foreign_user = User.objects.create_user(username="foreign-admin", password="secret")
        foreign_role = Role.objects.create(tenant=self.school_b, name="Admin", permissions=["attendance.session.manage", "attendance.any_class"])
        Membership.objects.create(tenant=self.school_b, user=foreign_user, role=foreign_role)
        foreign_session, _ = open_attendance_session(user=foreign_user, tenant=self.school_b, class_group=foreign_class, session_date=self.session_date)

        with self.assertRaises(ValidationError):
            record_attendance_bulk(user=self.admin, tenant=self.school_a, session=foreign_session, entries=[])

    def test_unrelated_activity_failure_rolls_back_the_correction(self):
        session, _ = self.open_session()
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.ABSENT}])
        with patch("apps.attendance.services.record_activity", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                                       entries=[{"student": self.student, "status": AttendanceStatus.PRESENT}])
        record = AttendanceRecord.objects.get(session=session, student=self.student)
        self.assertEqual(record.status, AttendanceStatus.ABSENT)
