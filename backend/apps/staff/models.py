import uuid

from django.db import models

from apps.documents.models import Document
from apps.tenancy.models import Campus, TenantOwnedModel, User


class EmploymentType(models.TextChoices):
    PERMANENT = "PERMANENT", "Permanent"
    CONTRACT = "CONTRACT", "Contract"
    PART_TIME = "PART_TIME", "Part-time"


class EmploymentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    SUSPENDED = "SUSPENDED", "Suspended"
    TERMINATED = "TERMINATED", "Terminated"


class Employee(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_number = models.CharField(max_length=40)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField(null=True, blank=True)
    national_id = models.CharField(max_length=40, blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, null=True, blank=True, related_name="employees")
    department = models.CharField(max_length=100, blank=True)
    job_title = models.CharField(max_length=100)
    employment_type = models.CharField(max_length=20, choices=EmploymentType.choices)
    hire_date = models.DateField()
    status = models.CharField(max_length=20, choices=EmploymentStatus.choices, default=EmploymentStatus.ACTIVE)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True)
    user_account = models.OneToOneField(
        User, on_delete=models.PROTECT, null=True, blank=True, related_name="employee_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "employee_number"], name="unique_employee_number_per_tenant")
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "campus", "status"]),
        ]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class EmployeeDocument(TenantOwnedModel):
    """document_type is Staff's own domain categorization (e.g. "ID_COPY");
    storage metadata (filename, size, checksum, uploader, upload time)
    lives on the referenced Document row -- a single source of truth
    shared with Students/Admissions rather than duplicated per domain.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=80)
    document = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")


class EmployeeQualification(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="qualifications")
    title = models.CharField(max_length=150)
    institution = models.CharField(max_length=150, blank=True)
    year_obtained = models.PositiveSmallIntegerField(null=True, blank=True)
