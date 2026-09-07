import uuid

from django.db import models

from apps.documents.models import Document
from apps.tenancy.models import TenantOwnedModel, User


class ApplicationStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"
    ENROLLED = "ENROLLED", "Enrolled"


class Application(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application_number = models.CharField(max_length=40)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=ApplicationStatus.choices, default=ApplicationStatus.DRAFT)
    campus = models.ForeignKey("tenancy.Campus", on_delete=models.PROTECT, null=True, blank=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "application_number"],
                name="unique_application_number_per_tenant",
            )
        ]


class ApplicationDocument(TenantOwnedModel):
    """document_type is Admissions' own domain categorization; storage
    metadata (including uploader/upload time) lives on the referenced
    Document row.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=80)
    document = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")