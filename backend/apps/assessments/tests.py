from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.academics.models import (
    AcademicLevel,
    AcademicYear,
    ClassGroup,
    EnrollmentStatus,
    StudentEnrollment,
    Subject,
    TeacherAssignment,
    Term,
)
from apps.activity.models import ActivityEvent
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import (
    Assessment,
    AssessmentGradingBand,
    AssessmentResult,
    AssessmentStatus,
    AssessmentType,
    GradingBand,
    GradingScheme,
    MarkStatus,
)
from .services import (
    add_grading_band,
    approve_assessment,
    create_assessment,
    publish_assessment,
    record_assessment_marks,
    reject_assessment_submission,
    reopen_approved_assessment,
    submit_assessment_for_approval,
)


class AssessmentFoundationTests(TestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")

        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="Admin",
            permissions=[
                "assessment.manage", "assessment.marks.manage", "assessment.result.amend",
                "assessment.record.view", "assessment.any_class", "assessment.approve", "assessment.publish",
                "assessment.setup.manage", "assessment.setup.view",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.admin_role)

        self.teacher = User.objects.create_user(username="teacher", password="secret")
        self.teacher_role = Role.objects.create(
            tenant=self.school_a, name="Teacher",
            permissions=["assessment.manage", "assessment.marks.manage", "assessment.result.amend", "assessment.record.view"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.teacher, role=self.teacher_role)

        self.other_teacher = User.objects.create_user(username="other-teacher", password="secret")
        Membership.objects.create(tenant=self.school_a, user=self.other_teacher, role=self.teacher_role)

        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.other_campus = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")
        self.year = AcademicYear.objects.create(tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        self.term = Term.objects.create(tenant=self.school_a, academic_year=self.year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        self.level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_group = ClassGroup.objects.create(tenant=self.school_a, name="Grade 8 East", code="G8-E", academic_level=self.level, campus=self.campus)
        self.subject = Subject.objects.create(tenant=self.school_a, name="Math", code="MATH")
        self.other_subject = Subject.objects.create(tenant=self.school_a, name="English", code="ENG")
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=self.teacher, class_group=self.class_group, subject=self.subject)
        self.assessment_type = AssessmentType.objects.create(tenant=self.school_a, name="CAT", code="CAT")

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

    def open_assessment(self, **overrides):
        values = dict(
            user=self.teacher, tenant=self.school_a, term=self.term, class_group=self.class_group,
            subject=self.subject, assessment_type=self.assessment_type, name="CAT 1",
            max_marks=Decimal("100"), scheduled_date=date(2026, 2, 1),
        )
        values.update(overrides)
        return create_assessment(**values)

    def make_grading_scheme(self, bands=(("A", 80, 100), ("B", 60, 79.99), ("C", 0, 59.99))):
        scheme = GradingScheme.objects.create(tenant=self.school_a, name="Standard", academic_level=self.level)
        for label, low, high in bands:
            add_grading_band(
                user=self.admin, tenant=self.school_a, scheme=scheme,
                grade_label=label, min_percentage=Decimal(str(low)), max_percentage=Decimal(str(high)),
            )
        return scheme


class RosterAndGradingSnapshotTests(AssessmentFoundationTests):
    def test_roster_is_snapshotted_and_immune_to_later_enrollment_changes(self):
        assessment, results = self.open_assessment()
        self.assertEqual({r.student_id for r in results}, {self.student.id, self.other_student.id})

        StudentEnrollment.objects.filter(tenant=self.school_a, student=self.other_student, academic_year=self.year).update(
            status=EnrollmentStatus.TRANSFERRED,
        )
        assessment_again, results_again = self.open_assessment()
        self.assertEqual(assessment.pk, assessment_again.pk)
        self.assertEqual({r.student_id for r in results_again}, {self.student.id, self.other_student.id})

    def test_grading_band_snapshot_is_immune_to_later_scheme_edits(self):
        scheme = self.make_grading_scheme()
        assessment, _ = self.open_assessment()
        self.assertEqual(assessment.grading_scheme_id, scheme.id)
        self.assertEqual(AssessmentGradingBand.objects.filter(assessment=assessment).count(), 3)

        # Editing setup afterward must not change how this assessment is graded.
        GradingBand.objects.filter(tenant=self.school_a, scheme=scheme, grade_label="A").update(min_percentage=Decimal("95"))

        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("85")}],
        )
        result = AssessmentResult.objects.get(assessment=assessment, student=self.student)
        self.assertEqual(result.grade, "A")  # frozen snapshot boundary (80-100), not the edited live one (95-100)

    def test_no_active_scheme_leaves_assessment_ungraded(self):
        assessment, _ = self.open_assessment()
        self.assertIsNone(assessment.grading_scheme)
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("70")}],
        )
        result = AssessmentResult.objects.get(assessment=assessment, student=self.student)
        self.assertEqual(result.grade, "")

    def test_a_second_active_scheme_for_the_same_level_is_rejected_at_the_db_level(self):
        GradingScheme.objects.create(tenant=self.school_a, name="Scheme A", academic_level=self.level, is_active=True)
        # A nested atomic() creates a savepoint so the expected IntegrityError
        # doesn't abort the whole test-wrapping transaction on PostgreSQL.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GradingScheme.objects.create(tenant=self.school_a, name="Scheme B", academic_level=self.level, is_active=True)

    def test_an_inactive_second_scheme_for_the_same_level_is_allowed(self):
        GradingScheme.objects.create(tenant=self.school_a, name="Scheme A", academic_level=self.level, is_active=True)
        inactive = GradingScheme.objects.create(tenant=self.school_a, name="Scheme B", academic_level=self.level, is_active=False)
        self.assertIsNotNone(inactive.pk)

    def test_deactivating_then_creating_a_new_active_scheme_is_allowed(self):
        first = GradingScheme.objects.create(tenant=self.school_a, name="Scheme A", academic_level=self.level, is_active=True)
        first.is_active = False
        first.save(update_fields=["is_active"])
        second = GradingScheme.objects.create(tenant=self.school_a, name="Scheme B", academic_level=self.level, is_active=True)
        self.assertIsNotNone(second.pk)

    def test_overlapping_grading_band_is_rejected(self):
        scheme = GradingScheme.objects.create(tenant=self.school_a, name="Standard", academic_level=self.level)
        add_grading_band(user=self.admin, tenant=self.school_a, scheme=scheme, grade_label="A", min_percentage=Decimal("80"), max_percentage=Decimal("100"))
        with self.assertRaisesMessage(ValidationError, "overlaps"):
            add_grading_band(user=self.admin, tenant=self.school_a, scheme=scheme, grade_label="B", min_percentage=Decimal("75"), max_percentage=Decimal("85"))


class AuthorizationTests(AssessmentFoundationTests):
    def test_assigned_teacher_for_the_right_subject_can_create(self):
        assessment, _ = self.open_assessment(user=self.teacher)
        self.assertIsNotNone(assessment)

    def test_wrong_subject_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "not assigned to this class and subject"):
            self.open_assessment(user=self.teacher, subject=self.other_subject)

    def test_unassigned_teacher_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "not assigned to this class and subject"):
            self.open_assessment(user=self.other_teacher)

    def test_any_class_permission_bypasses_the_assignment_check(self):
        assessment, _ = self.open_assessment(user=self.admin)
        self.assertIsNotNone(assessment)

    def test_any_class_does_not_bypass_campus_scope(self):
        other_campus_class = ClassGroup.objects.create(
            tenant=self.school_a, name="Annex Grade 8", code="G8-ANNEX", academic_level=self.level, campus=self.other_campus,
        )
        Membership.objects.filter(tenant=self.school_a, user=self.admin).update(campus=self.campus)
        with self.assertRaisesMessage(ValidationError, "not authorized for this campus"):
            self.open_assessment(user=self.admin, class_group=other_campus_class)


class MarksEntryValidationTests(AssessmentFoundationTests):
    def test_score_out_of_bounds_is_rejected(self):
        assessment, _ = self.open_assessment()
        with self.assertRaisesMessage(ValidationError, "score must be between"):
            record_assessment_marks(
                user=self.teacher, tenant=self.school_a, assessment=assessment,
                entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("150")}],
            )

    def test_score_required_when_scored(self):
        assessment, _ = self.open_assessment()
        with self.assertRaisesMessage(ValidationError, "score must be between"):
            record_assessment_marks(
                user=self.teacher, tenant=self.school_a, assessment=assessment,
                entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": None}],
            )

    def test_score_forbidden_unless_scored(self):
        assessment, _ = self.open_assessment()
        with self.assertRaisesMessage(ValidationError, "score must be empty"):
            record_assessment_marks(
                user=self.teacher, tenant=self.school_a, assessment=assessment,
                entries=[{"student": self.student, "mark_status": MarkStatus.ABSENT, "score": Decimal("10")}],
            )

    def test_student_not_on_roster_is_rejected(self):
        assessment, _ = self.open_assessment()
        outsider = Student.objects.create(tenant=self.school_a, admission_number="ADM-999", first_name="Not", last_name="Enrolled")
        with self.assertRaisesMessage(ValidationError, "is not on this assessment's roster"):
            record_assessment_marks(
                user=self.teacher, tenant=self.school_a, assessment=assessment,
                entries=[{"student": outsider, "mark_status": MarkStatus.ABSENT}],
            )

    def test_not_marked_placeholders_have_no_recorder(self):
        _, results = self.open_assessment()
        for result in results:
            self.assertIsNone(result.recorded_by_id)
            self.assertEqual(result.mark_status, MarkStatus.NOT_MARKED)


class AuditTests(AssessmentFoundationTests):
    def test_first_entry_off_not_marked_is_not_a_correction(self):
        assessment, _ = self.open_assessment()
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("70")}],
        )
        self.assertEqual(ActivityEvent.objects.count(), 0)

    def test_correction_is_audited_with_full_field_comparison(self):
        assessment, _ = self.open_assessment()
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("70")}],
        )
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("85"), "remarks": "Re-marked after review"}],
        )
        self.assertEqual(ActivityEvent.objects.count(), 1)
        event = ActivityEvent.objects.get()
        self.assertEqual(event.action, "assessment.result.corrected")
        self.assertEqual(Decimal(event.metadata["previous"]["score"]), Decimal("70"))
        self.assertEqual(Decimal(event.metadata["new"]["score"]), Decimal("85"))
        self.assertEqual(event.metadata["new"]["remarks"], "Re-marked after review")

    def test_remarks_only_change_is_audited(self):
        assessment, _ = self.open_assessment()
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.ABSENT}],
        )
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.ABSENT, "remarks": "Called in sick"}],
        )
        self.assertEqual(ActivityEvent.objects.count(), 1)

    def test_resubmitting_identical_values_is_a_no_op(self):
        assessment, _ = self.open_assessment()
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("70")}],
        )
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("70")}],
        )
        self.assertEqual(ActivityEvent.objects.count(), 0)


class LifecycleTests(AssessmentFoundationTests):
    def mark_all(self, assessment, score=Decimal("70")):
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[
                {"student": self.student, "mark_status": MarkStatus.SCORED, "score": score},
                {"student": self.other_student, "mark_status": MarkStatus.SCORED, "score": score},
            ],
        )

    def test_submit_requires_completeness(self):
        assessment, _ = self.open_assessment()
        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=assessment,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("70")}],
        )
        with self.assertRaisesMessage(ValidationError, "must be marked"):
            submit_assessment_for_approval(user=self.teacher, tenant=self.school_a, assessment=assessment)

    def test_full_happy_path(self):
        assessment, _ = self.open_assessment()
        self.mark_all(assessment)
        submitted = submit_assessment_for_approval(user=self.teacher, tenant=self.school_a, assessment=assessment)
        self.assertEqual(submitted.status, AssessmentStatus.SUBMITTED)

        approved = approve_assessment(user=self.admin, tenant=self.school_a, assessment=submitted)
        self.assertEqual(approved.status, AssessmentStatus.APPROVED)

        published = publish_assessment(user=self.admin, tenant=self.school_a, assessment=approved)
        self.assertEqual(published.status, AssessmentStatus.PUBLISHED)
        self.assertIsNotNone(published.published_at)

    def test_reject_sends_back_to_draft(self):
        assessment, _ = self.open_assessment()
        self.mark_all(assessment)
        submitted = submit_assessment_for_approval(user=self.teacher, tenant=self.school_a, assessment=assessment)
        rejected = reject_assessment_submission(user=self.admin, tenant=self.school_a, assessment=submitted, reason="Suspicious scores")
        self.assertEqual(rejected.status, AssessmentStatus.DRAFT)
        event = ActivityEvent.objects.get(action="assessment.rejected")
        self.assertEqual(event.metadata["reason"], "Suspicious scores")

    def test_reopen_sends_approved_back_to_draft(self):
        assessment, _ = self.open_assessment()
        self.mark_all(assessment)
        submitted = submit_assessment_for_approval(user=self.teacher, tenant=self.school_a, assessment=assessment)
        approved = approve_assessment(user=self.admin, tenant=self.school_a, assessment=submitted)
        reopened = reopen_approved_assessment(user=self.admin, tenant=self.school_a, assessment=approved, reason="Found a transcription error")
        self.assertEqual(reopened.status, AssessmentStatus.DRAFT)

    def test_marks_cannot_be_edited_while_submitted(self):
        assessment, _ = self.open_assessment()
        self.mark_all(assessment)
        submitted = submit_assessment_for_approval(user=self.teacher, tenant=self.school_a, assessment=assessment)
        with self.assertRaisesMessage(ValidationError, "Marks cannot be edited"):
            record_assessment_marks(
                user=self.teacher, tenant=self.school_a, assessment=submitted,
                entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("99")}],
            )

    def test_marks_cannot_be_edited_while_approved(self):
        assessment, _ = self.open_assessment()
        self.mark_all(assessment)
        submitted = submit_assessment_for_approval(user=self.teacher, tenant=self.school_a, assessment=assessment)
        approved = approve_assessment(user=self.admin, tenant=self.school_a, assessment=submitted)
        with self.assertRaisesMessage(ValidationError, "Marks cannot be edited"):
            record_assessment_marks(
                user=self.teacher, tenant=self.school_a, assessment=approved,
                entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("99")}],
            )

    def test_published_amendment_requires_amend_permission_and_stamps_last_amended_at(self):
        assessment, _ = self.open_assessment()
        self.mark_all(assessment)
        submitted = submit_assessment_for_approval(user=self.teacher, tenant=self.school_a, assessment=assessment)
        approved = approve_assessment(user=self.admin, tenant=self.school_a, assessment=submitted)
        published = publish_assessment(user=self.admin, tenant=self.school_a, assessment=approved)
        self.assertIsNone(published.last_amended_at)

        no_amend_role = Role.objects.create(
            tenant=self.school_a, name="NoAmend",
            permissions=["assessment.manage", "assessment.marks.manage", "assessment.record.view"],
        )
        limited_teacher = User.objects.create_user(username="limited", password="secret")
        Membership.objects.create(tenant=self.school_a, user=limited_teacher, role=no_amend_role)
        TeacherAssignment.objects.create(tenant=self.school_a, teacher=limited_teacher, class_group=self.class_group, subject=self.subject)
        with self.assertRaisesMessage(ValidationError, "User lacks permission: assessment.result.amend"):
            record_assessment_marks(
                user=limited_teacher, tenant=self.school_a, assessment=published,
                entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("99")}],
            )

        record_assessment_marks(
            user=self.teacher, tenant=self.school_a, assessment=published,
            entries=[{"student": self.student, "mark_status": MarkStatus.SCORED, "score": Decimal("99")}],
        )
        published.refresh_from_db()
        self.assertIsNotNone(published.last_amended_at)
        event = ActivityEvent.objects.get(action="assessment.result.corrected")
        self.assertIn("grade", event.metadata["previous"])

    def test_approve_rechecks_completeness(self):
        assessment, _ = self.open_assessment()
        self.mark_all(assessment)
        submitted = submit_assessment_for_approval(user=self.teacher, tenant=self.school_a, assessment=assessment)
        # Simulate a result somehow reverting to NOT_MARKED between submit and approve.
        AssessmentResult.objects.filter(assessment=submitted, student=self.student).update(mark_status=MarkStatus.NOT_MARKED)
        with self.assertRaisesMessage(ValidationError, "must be marked"):
            approve_assessment(user=self.admin, tenant=self.school_a, assessment=submitted)

    def test_cross_tenant_assessment_is_rejected(self):
        foreign_campus = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        foreign_year = AcademicYear.objects.create(tenant=self.school_b, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        foreign_term = Term.objects.create(tenant=self.school_b, academic_year=foreign_year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        foreign_level = AcademicLevel.objects.create(tenant=self.school_b, name="Grade 8", code="G8", sequence=8)
        foreign_class = ClassGroup.objects.create(tenant=self.school_b, name="Grade 8", code="G8", academic_level=foreign_level, campus=foreign_campus)
        foreign_subject = Subject.objects.create(tenant=self.school_b, name="Math", code="MATH")
        foreign_type = AssessmentType.objects.create(tenant=self.school_b, name="CAT", code="CAT")
        foreign_user = User.objects.create_user(username="foreign-admin", password="secret")
        foreign_role = Role.objects.create(tenant=self.school_b, name="Admin", permissions=["assessment.manage", "assessment.any_class"])
        Membership.objects.create(tenant=self.school_b, user=foreign_user, role=foreign_role)

        with self.assertRaises(ValidationError):
            create_assessment(
                user=foreign_user, tenant=self.school_a, term=foreign_term, class_group=foreign_class,
                subject=foreign_subject, assessment_type=foreign_type, name="CAT 1",
                max_marks=Decimal("100"), scheduled_date=date(2026, 2, 1),
            )
