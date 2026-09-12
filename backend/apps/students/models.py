import uuid

from django.db import models

from apps.documents.models import Document
from apps.tenancy.models import Campus, TenantOwnedModel


class StudentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    SUSPENDED = "SUSPENDED", "Suspended"
    TRANSFERRED = "TRANSFERRED", "Transferred"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"
    GRADUATED = "GRADUATED", "Graduated"


class Student(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    admission_number = models.CharField(max_length=40)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField(null=True, blank=True)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=20, choices=StudentStatus.choices, default=StudentStatus.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "admission_number"],
                name="unique_admission_number_per_tenant",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "campus", "status"]),
        ]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class StudentDocument(TenantOwnedModel):
    """document_type is Students' own domain categorization (e.g. "Birth
    certificate"); storage metadata lives on the referenced Document row.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=80)
    document = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")