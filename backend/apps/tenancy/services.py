from django.core.exceptions import ValidationError
from django.db.models import F

from .models import Membership


def require_membership(*, user, tenant=None, tenant_slug=None):
    """Return an active membership or reject access to the tenant."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        raise ValidationError("An active authenticated user is required")
    if (tenant is None) == (tenant_slug is None):
        raise ValidationError("Exactly one tenant context is required")
    scope = {"tenant": tenant} if tenant is not None else {"tenant__slug": tenant_slug}
    membership = Membership.objects.filter(
        **scope,
        tenant__is_active=True,
        role__tenant_id=F("tenant_id"),
        user=user,
        user__is_active=True,
        is_active=True,
    ).select_related("role", "tenant").first()
    if membership is None:
        raise ValidationError("User does not have an active membership in this tenant")
    return membership


def require_permission(*, user, permission, tenant=None, tenant_slug=None):
    membership = require_membership(user=user, tenant=tenant, tenant_slug=tenant_slug)
    if not getattr(user, "is_superuser", False) and permission not in membership.role.permissions:
        raise ValidationError(f"User lacks permission: {permission}")
    return membership


def require_same_tenant(*, tenant, **objects):
    """Reject a domain operation when any tenant-owned object crosses boundaries."""
    mismatched = [name for name, value in objects.items() if value.tenant_id != tenant.id]
    if mismatched:
        names = ", ".join(sorted(mismatched))
        raise ValidationError(f"Objects belong to a different tenant: {names}")
