from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, EnrollmentStatus, StudentEnrollment, Subject, TeacherAssignment
from apps.activity.models import ActivityEvent
from apps.guardians.models import Guardian, StudentGuardian
from apps.notifications.models import NotificationChannel, NotificationEvent, NotificationOutbox, NotificationRecipientType
from apps.notifications.services import create_notification_rule, create_notification_template, expand_notification_event, set_channel_enabled
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import AttendanceRecord, AttendanceSession, AttendanceSessionStatus, AttendanceSetup, AttendanceStatus
from .services import open_attendance_session, record_attendance_bulk, submit_attendance_session


class AttendanceFoundationTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="Admin",
            permissions=[
                "attendance.session.manage", "attendance.record.view", "attendance.any_class",
                "attendance.session.override_calendar",
                "notifications.setup.manage", "notifications.templates.manage", "notifications.rules.manage",
            ],
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
        self.other_campus = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")
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

        session, records = self.open_session()

        roster_student_ids = {record.student_id for record in records}
        self.assertEqual(roster_student_ids, {self.student.id, self.other_student.id})

    def test_no_covering_academic_year_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "No academic year covers this date"):
            self.open_session(session_date=date(2030, 1, 1))

    def test_roster_is_snapshotted_and_immune_to_later_enrollment_changes(self):
        session, records = self.open_session()
        self.assertEqual({r.student_id for r in records}, {self.student.id, self.other_student.id})

        # A later correction to enrollment must not retroactively change who
        # this already-opened session expected on the roster.
        StudentEnrollment.objects.filter(tenant=self.school_a, student=self.other_student, academic_year=self.year).update(
            status=EnrollmentStatus.TRANSFERRED,
        )
        _, records_again = open_attendance_session(
            user=self.teacher, tenant=self.school_a, class_group=self.class_group, session_date=self.session_date,
        )
        self.assertEqual({r.student_id for r in records_again}, {self.student.id, self.other_student.id})


class SessionOpenTests(AttendanceFoundationTests):
    def test_open_is_idempotent(self):
        first, _ = self.open_session()
        second, _ = self.open_session()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AttendanceSession.objects.count(), 1)

    def test_open_materializes_not_marked_placeholder_records(self):
        session, records = self.open_session()
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record.status, AttendanceStatus.NOT_MARKED)
            self.assertIsNone(record.recorded_by_id)
        self.assertEqual(AttendanceRecord.objects.filter(session=session).count(), 2)

    def test_non_instructional_day_is_rejected_without_force(self):
        saturday = date(2026, 3, 7)
        with self.assertRaisesMessage(ValidationError, "not an instructional day"):
            self.open_session(session_date=saturday)
        self.assertEqual(AttendanceSession.objects.count(), 0)

    def test_non_instructional_day_can_be_forced_with_override_permission(self):
        saturday = date(2026, 3, 7)
        session, _ = self.open_session(user=self.admin, session_date=saturday, force=True)
        self.assertEqual(session.session_date, saturday)
        self.assertEqual(
            ActivityEvent.objects.filter(action="attendance.session.calendar_overridden").count(), 1,
        )

    def test_force_without_override_permission_is_rejected(self):
        saturday = date(2026, 3, 7)
        with self.assertRaisesMessage(ValidationError, "User lacks permission: attendance.session.override_calendar"):
            self.open_session(user=self.teacher, session_date=saturday, force=True)
        self.assertEqual(AttendanceSession.objects.count(), 0)

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

    def test_campus_scoped_membership_is_rejected_for_a_different_campus_class(self):
        other_campus_class = ClassGroup.objects.create(
            tenant=self.school_a, name="Annex Grade 8", code="G8-ANNEX", academic_level=self.level, campus=self.other_campus,
        )
        Membership.objects.filter(tenant=self.school_a, user=self.teacher).update(campus=self.campus)
        with self.assertRaisesMessage(ValidationError, "not authorized for this campus"):
            open_attendance_session(
                user=self.teacher, tenant=self.school_a, class_group=other_campus_class, session_date=self.session_date,
            )

    def test_any_class_does_not_bypass_campus_scope(self):
        other_campus_class = ClassGroup.objects.create(
            tenant=self.school_a, name="Annex Grade 8", code="G8-ANNEX", academic_level=self.level, campus=self.other_campus,
        )
        Membership.objects.filter(tenant=self.school_a, user=self.admin).update(campus=self.campus)
        with self.assertRaisesMessage(ValidationError, "not authorized for this campus"):
            open_attendance_session(
                user=self.admin, tenant=self.school_a, class_group=other_campus_class, session_date=self.session_date,
            )


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

    def test_marking_absent_notifies_the_primary_guardian_once_not_on_a_no_op_resubmission(self):
        guardian = Guardian.objects.create(tenant=self.school_a, first_name="Rose", last_name="Otieno", phone_number="0700000001")
        StudentGuardian.objects.create(tenant=self.school_a, student=self.student, guardian=guardian, relationship="Mother", is_primary=True)
        set_channel_enabled(user=self.admin, tenant=self.school_a, channel=NotificationChannel.SMS, enabled=True)
        template = create_notification_template(
            user=self.admin, tenant=self.school_a, code="ABSENT_SMS", name="Absent", channel=NotificationChannel.SMS,
            body="Dear {{ guardian_name }}, your child was marked absent on {{ session_date }}.",
        )
        create_notification_rule(
            user=self.admin, tenant=self.school_a, event_code="attendance.student.absent",
            recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template,
        )
        session, _ = self.open_session()

        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.ABSENT}])
        for event in NotificationEvent.objects.filter(tenant=self.school_a):
            expand_notification_event(event=event)
        self.assertEqual(NotificationOutbox.objects.filter(tenant=self.school_a).count(), 1)

        # Remarks-only correction while remaining ABSENT must not re-notify.
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.ABSENT, "remarks": "Called in sick"}])
        for event in NotificationEvent.objects.filter(tenant=self.school_a):
            expand_notification_event(event=event)
        self.assertEqual(NotificationOutbox.objects.filter(tenant=self.school_a).count(), 1)

    def test_student_not_on_the_roster_is_rejected(self):
        session, _ = self.open_session()
        outsider = Student.objects.create(tenant=self.school_a, admission_number="ADM-999", first_name="Not", last_name="Enrolled")
        with self.assertRaisesMessage(ValidationError, "is not enrolled in this class"):
            record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                                   entries=[{"student": outsider, "status": AttendanceStatus.PRESENT}])
        # The two roster placeholders from session-open still exist -- only
        # the outsider's entry was rejected.
        self.assertEqual(AttendanceRecord.objects.count(), 2)

    def test_status_correction_updates_the_record_and_logs_activity(self):
        session, _ = self.open_session()
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.ABSENT}])
        self.assertEqual(ActivityEvent.objects.count(), 0)  # first-time entry off NOT_MARKED is not a "correction"

        record_attendance_bulk(user=self.admin, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.PRESENT, "remarks": "Arrived late, marked present"}])

        record = AttendanceRecord.objects.get(session=session, student=self.student)
        self.assertEqual(record.status, AttendanceStatus.PRESENT)
        self.assertEqual(ActivityEvent.objects.count(), 1)
        event = ActivityEvent.objects.get()
        self.assertEqual(event.action, "attendance.record.corrected")
        self.assertEqual(event.metadata, {
            "previous": {"status": AttendanceStatus.ABSENT, "remarks": ""},
            "new": {"status": AttendanceStatus.PRESENT, "remarks": "Arrived late, marked present"},
        })

    def test_remarks_only_change_is_audited(self):
        session, _ = self.open_session()
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.ABSENT}])
        self.assertEqual(ActivityEvent.objects.count(), 0)

        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.ABSENT, "remarks": "Parent called; medical appointment"}])

        self.assertEqual(ActivityEvent.objects.count(), 1)
        event = ActivityEvent.objects.get()
        self.assertEqual(event.metadata["previous"], {"status": AttendanceStatus.ABSENT, "remarks": ""})
        self.assertEqual(event.metadata["new"], {"status": AttendanceStatus.ABSENT, "remarks": "Parent called; medical appointment"})

    def test_resubmitting_the_same_status_is_a_no_op_for_the_audit_log(self):
        session, _ = self.open_session()
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.PRESENT}])
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.PRESENT}])
        self.assertEqual(ActivityEvent.objects.count(), 0)

    def test_corrections_remain_allowed_after_session_submission(self):
        session, _ = self.open_session()
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[
                                   {"student": self.student, "status": AttendanceStatus.PRESENT},
                                   {"student": self.other_student, "status": AttendanceStatus.PRESENT},
                               ])
        submit_attendance_session(user=self.teacher, tenant=self.school_a, session=session)

        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.LATE}])
        record = AttendanceRecord.objects.get(session=session, student=self.student)
        self.assertEqual(record.status, AttendanceStatus.LATE)
        self.assertEqual(ActivityEvent.objects.filter(action="attendance.record.corrected").count(), 1)

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


class SubmitAttendanceSessionTests(AttendanceFoundationTests):
    def mark_all(self, session, status=AttendanceStatus.PRESENT):
        record_attendance_bulk(
            user=self.teacher, tenant=self.school_a, session=session,
            entries=[{"student": self.student, "status": status}, {"student": self.other_student, "status": status}],
        )

    def test_submit_requires_every_roster_student_to_be_marked(self):
        session, _ = self.open_session()
        record_attendance_bulk(user=self.teacher, tenant=self.school_a, session=session,
                               entries=[{"student": self.student, "status": AttendanceStatus.PRESENT}])
        with self.assertRaisesMessage(ValidationError, "must be marked before submission"):
            submit_attendance_session(user=self.teacher, tenant=self.school_a, session=session)

    def test_submit_succeeds_once_complete_and_logs_activity(self):
        session, _ = self.open_session()
        self.mark_all(session)
        submitted = submit_attendance_session(user=self.teacher, tenant=self.school_a, session=session)
        self.assertEqual(submitted.status, AttendanceSessionStatus.SUBMITTED)
        self.assertEqual(submitted.submitted_by_id, self.teacher.id)
        self.assertIsNotNone(submitted.submitted_at)
        self.assertEqual(ActivityEvent.objects.filter(action="attendance.session.submitted").count(), 1)

    def test_submitting_twice_is_rejected(self):
        session, _ = self.open_session()
        self.mark_all(session)
        submit_attendance_session(user=self.teacher, tenant=self.school_a, session=session)
        with self.assertRaisesMessage(ValidationError, "already been submitted"):
            submit_attendance_session(user=self.teacher, tenant=self.school_a, session=session)
