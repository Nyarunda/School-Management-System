import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class TenantOwnedQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        if tenant is None:
            raise ValueError("A tenant is required for tenant-scoped access")
        return self.filter(tenant=tenant)


class TenantOwnedModel(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="%(class)ss")
    objects = TenantOwnedQuerySet.as_manager()

    class Meta:
        abstract = True


class Campus(TenantOwnedModel):
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=30)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="unique_campus_code_per_tenant")
        ]


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class Role(TenantOwnedModel):
    name = models.CharField(max_length=100)
    permissions = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="unique_role_name_per_tenant")
        ]


class Membership(TenantOwnedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="memberships")
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user"], name="unique_user_membership_per_tenant")
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.role.tenant_id != self.tenant_id:
            raise ValidationError("Membership role must belong to the same tenant")
        if self.campus_id and self.campus.tenant_id != self.tenant_id:
            raise ValidationError("Membership campus must belong to the same tenant")


class AuditEvent(TenantOwnedModel):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=120)
    resource_type = models.CharField(max_length=120)
    resource_id = models.CharField(max_length=120)
    request_id = models.CharField(max_length=120, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)