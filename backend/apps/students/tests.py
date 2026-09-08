from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.documents.testing import TemporaryDocumentStorageMixin, make_upload
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import Student, StudentStatus
from .selectors import enrollment_register_rows
from .services import add_student_document, change_student_status, delete_student_document, place_student


class StudentLifecycleTests(TemporaryDocumentStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.campus_a = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.campus_b = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")
        self.student = Student.objects.create(
            tenant=self.school_a,
            admission_number="ADM-001",
            first_name="Amina",
            last_name="Otieno",
        )
        self.admin = User.objects.create_user(username="admin", password="secret")
        role = Role.objects.create(
            tenant=self.school_a, name="Registrar", permissions=["students.document.view", "students.document.manage"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.admin, role=role)

    def test_terminal_student_states_cannot_be_reopened(self):
        change_student_status(student=self.student, status=StudentStatus.GRADUATED)

        with self.assertRaises(ValidationError):
            change_student_status(student=self.student, status=StudentStatus.ACTIVE)

    def test_student_cannot_be_placed_on_another_tenants_campus(self):
        with self.assertRaises(ValidationError):
            place_student(student=self.student, campus=self.campus_b)

        self.student.refresh_from_db()
        self.assertIsNone(self.student.campus)

    def test_document_is_created_through_student_tenant_boundary(self):
        document = add_student_document(
            user=self.admin,
            tenant=self.school_a,
            student=self.student,
            document_type="Birth certificate",
            file_obj=make_upload(name="birth-certificate.pdf"),
            original_filename="birth-certificate.pdf",
            content_type="application/pdf",
        )

        self.assertEqual(document.tenant, self.school_a)
        self.assertEqual(self.student.documents.count(), 1)
        self.assertEqual(document.document.original_filename, "birth-certificate.pdf")

    def test_delete_student_document(self):
        document = add_student_document(
            user=self.admin, tenant=self.school_a, student=self.student, document_type="Birth certificate",
            file_obj=make_upload(), original_filename="birth-certificate.pdf", content_type="application/pdf",
        )
        delete_student_document(user=self.admin, tenant=self.school_a, student_document=document)
        self.assertEqual(self.student.documents.count(), 0)

    def test_document_upload_requires_permission(self):
        outsider = User.objects.create_user(username="outsider", password="secret")
        Membership.objects.create(
            tenant=self.school_a, user=outsider,
            role=Role.objects.create(tenant=self.school_a, name="No Access", permissions=[]),
        )
        with self.assertRaises(ValidationError):
            add_student_document(
                user=outsider, tenant=self.school_a, student=self.student, document_type="Birth certificate",
                file_obj=make_upload(), original_filename="birth-certificate.pdf", content_type="application/pdf",
            )


class EnrollmentRegisterRowsTests(TestCase):
    """Milestone 20 -- the query function apps.reporting's catalogue calls
    for students.enrollment_register.
    """

    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.other_campus = Campus.objects.create(tenant=self.school_a, name="Annex", code="ANNEX")
        self.active = Student.objects.create(
            tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno",
            campus=self.campus, status=StudentStatus.ACTIVE,
        )
        self.graduated = Student.objects.create(
            tenant=self.school_a, admission_number="ADM-002", first_name="Brian", last_name="Kiptoo",
            campus=self.other_campus, status=StudentStatus.GRADUATED,
        )

    def test_returns_all_students_by_default(self):
        rows = enrollment_register_rows(tenant=self.school_a)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["admission_number"], "ADM-001")
        self.assertEqual(rows[0]["campus"], "Main")

    def test_filters_by_campus(self):
        rows = enrollment_register_rows(tenant=self.school_a, campus_id=str(self.campus.id))
        self.assertEqual([row["admission_number"] for row in rows], ["ADM-001"])

    def test_filters_by_status(self):
        rows = enrollment_register_rows(tenant=self.school_a, status=StudentStatus.GRADUATED)
        self.assertEqual([row["admission_number"] for row in rows], ["ADM-002"])