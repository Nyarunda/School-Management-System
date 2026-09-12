import uuid

from django.db import models

from apps.tenancy.models import TenantOwnedModel, User


class DocumentSetup(TenantOwnedModel):
    """One per tenant. default_retention_days is snapshotted onto each
    Document at upload time (retention_expires_at) -- changing this
    setting only affects documents uploaded after the change, never
    retroactively re-dating documents already stored.
    """

    default_retention_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="unique_document_setup_per_tenant")]


class Document(TenantOwnedModel):
    """Storage metadata owned by the Documents module. Business domains
    (Student/Staff/Admissions) own what a document MEANS via their own FK
    to this row -- this model only knows how to store and retrieve bytes.
    storage_key is a bare UUID+extension with no tenant prefix baked in;
    the storage backend itself owns tenant namespacing (see
    storage/local.py), so a caller bug can never smuggle a key that reads
    or overwrites another tenant's file. size_bytes/checksum_sha256 are
    always the actual streamed values, never a client-supplied claim.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    storage_key = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    retention_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "storage_key"], name="unique_storage_key_per_tenant")]
        indexes = [models.Index(fields=["tenant", "retention_expires_at"])]
