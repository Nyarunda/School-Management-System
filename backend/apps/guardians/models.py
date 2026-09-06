import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.students.models import Student
from apps.tenancy.models import TenantOwnedModel


class Guardian(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=40)
    email = models.EmailField(blank=True)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class StudentGuardian(TenantOwnedModel):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="guardian_links")
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name="student_links")
    relationship = models.CharField(max_length=60)
    is_primary = models.BooleanField(default=False)
    is_emergency_contact = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "student", "guardian"], name="unique_student_guardian_per_tenant"),
        ]

    def clean(self):
        if self.student.tenant_id != self.tenant_id or self.guardian.tenant_id != self.tenant_id:
            raise ValidationError("Student and guardian must belong to the same tenant")