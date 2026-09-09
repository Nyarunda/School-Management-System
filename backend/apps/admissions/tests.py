from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.academics.models import AcademicLevel, AcademicYear, ClassGroup, StudentEnrollment, Term
from apps.documents.testing import TemporaryDocumentStorageMixin, make_upload
from apps.students.models import Student
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Application, ApplicationStatus
from .services import add_application_document, delete_application_document, enroll_application, transition_application


class AdmissionLifecycleTests(TemporaryDocumentStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.campus_a = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.campus_a2 = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")
        self.campus_b = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        self.reviewer = User.objects.create_user(username="reviewer", password="secret")
        self.application = Application.objects.create(
            tenant=self.school_a,
            application_number="APP-001",
            first_name="Amina",
            last_name="Otieno",
            date_of_birth=date(2014, 5, 10),
            campus=self.campus_a,
        )
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a, name="Admissions Officer",
            permissions=["admissions.document.manage", "admissions.enroll", "academics.students.enroll"],
        )
        self.membership = Membership.objects.create(tenant=self.school_a, user=self.admin, role=self.role)

        self.year_a = AcademicYear.objects.create(
            tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31), is_current=True,
        )
        self.term_a = Term.objects.create(
            tenant=self.school_a, academic_year=self.year_a, name="Term 1",
            starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1,
        )
        self.level_a = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.class_a = ClassGroup.objects.create(
            tenant=self.school_a, name="Grade 8 East", code="G8-E",
            academic_level=self.level_a, campus=self.campus_a, stream="East",
        )

    def enroll(self, **overrides):
        values = dict(
            user=self.admin, tenant=self.school_a, application=self.application,
            admission_number="ADM-001", academic_year=self.year_a, class_group=self.class_a,
        )
        values.update(overrides)
        return enroll_application(**values)

    def test_application_requires_ordered_status_transitions(self):
        transition_application(application=self.application, status=ApplicationStatus.SUBMITTED)
        transition_application(application=self.application, status=ApplicationStatus.UNDER_REVIEW)
        transition_application(application=self.application, status=ApplicationStatus.ACCEPTED, reviewer=self.reviewer)

        self.assertEqual(self.application.status, ApplicationStatus.ACCEPTED)
        with self.assertRaises(ValidationError):
            transition_application(application=self.application, status=ApplicationStatus.REJECTED)

    def test_enrollment_creates_student_and_placement_and_closes_application(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])

        student, enrollment = self.enroll()

        self.assertEqual(student.tenant, self.school_a)
        self.assertEqual(student.admission_number, "ADM-001")
        self.assertEqual(student.campus, self.campus_a)
        self.assertEqual(enrollment.student_id, student.id)
        self.assertEqual(enrollment.class_group_id, self.class_a.id)
        self.assertEqual(enrollment.academic_year_id, self.year_a.id)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.ENROLLED)
        self.assertEqual(StudentEnrollment.objects.count(), 1)

    def test_enrollment_requires_admissions_enroll_permission(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])
        self.role.permissions = ["academics.students.enroll"]
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertEqual(Student.objects.count(), 0)

    def test_enrollment_requires_academics_students_enroll_permission(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])
        self.role.permissions = ["admissions.enroll"]
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            self.enroll()
        # admissions.enroll passes the outer check, so the Student row is
        # inserted before enroll_student's own internal permission check
        # fails -- the whole call is one atomic transaction, so it rolls
        # back and nothing persists.
        self.assertEqual(Student.objects.count(), 0)

    def test_cross_tenant_class_cannot_be_used_for_enrollment(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])
        foreign_level = AcademicLevel.objects.create(tenant=self.school_b, name="Grade 8", code="G8", sequence=8)
        foreign_class = ClassGroup.objects.create(
            tenant=self.school_b, name="Grade 8", code="G8", academic_level=foreign_level, campus=self.campus_b,
        )

        with self.assertRaises(ValidationError):
            self.enroll(class_group=foreign_class)
        self.assertEqual(Student.objects.count(), 0)

    def test_campus_scoped_registrar_cannot_enroll_into_a_different_campus(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])
        self.membership.campus = self.campus_a2
        self.membership.save(update_fields=["campus"])

        # class_a belongs to campus_a, not the registrar's campus_a2 -- even
        # though the Application itself also belongs to campus_a.
        with self.assertRaises(ValidationError):
            self.enroll()
        self.assertEqual(Student.objects.count(), 0)

    def test_campus_scoped_registrar_can_enroll_within_their_own_campus(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])
        self.membership.campus = self.campus_a
        self.membership.save(update_fields=["campus"])

        student, enrollment = self.enroll()
        self.assertEqual(student.campus, self.campus_a)

    def test_campus_override_must_match_the_selected_class(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            self.enroll(campus=self.campus_a2)
        self.assertEqual(Student.objects.count(), 0)

    def test_rejected_application_cannot_be_enrolled(self):
        self.application.status = ApplicationStatus.REJECTED
        self.application.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            self.enroll()

    def test_application_cannot_be_enrolled_twice(self):
        self.application.status = ApplicationStatus.ACCEPTED
        self.application.save(update_fields=["status"])
        self.enroll()

        with self.assertRaises(ValidationError):
            self.enroll(admission_number="ADM-002")
        self.assertEqual(Student.objects.count(), 1)


class ApplicationDocumentTests(TemporaryDocumentStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.application = Application.objects.create(
            tenant=self.school_a, application_number="APP-001", first_name="Amina", last_name="Otieno",
        )
        self.admin = User.objects.create_user(username="admin", password="secret")
        role = Role.objects.create(tenant=self.school_a, name="Admissions Officer", permissions=["admissions.document.manage"])
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=role)

    def test_add_and_delete_application_document(self):
        document = add_application_document(
            user=self.admin, tenant=self.school_a, application=self.application, document_type="Birth certificate",
            file_obj=make_upload(name="birth.pdf"), original_filename="birth.pdf", content_type="application/pdf",
        )
        self.assertEqual(document.application_id, self.application.id)
        self.assertEqual(document.document.original_filename, "birth.pdf")
        self.assertEqual(self.application.documents.count(), 1)

        delete_application_document(user=self.admin, tenant=self.school_a, application_document=document)
        self.assertEqual(self.application.documents.count(), 0)

    def test_add_document_requires_permission(self):
        outsider = User.objects.create_user(username="outsider", password="secret")
        Membership.objects.create(
            tenant=self.school_a, user=outsider,
            role=Role.objects.create(tenant=self.school_a, name="No Access", permissions=[]),
        )
        with self.assertRaises(ValidationError):
            add_application_document(
                user=outsider, tenant=self.school_a, application=self.application, document_type="Birth certificate",
                file_obj=make_upload(), original_filename="birth.pdf", content_type="application/pdf",
            )
