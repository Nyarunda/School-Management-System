from django.core.exceptions import ValidationError

from .models import Membership


def require_membership(*, user, tenant):
    """Return an active membership or reject access to the tenant."""
    membership = Membership.objects.filter(
        tenant=tenant,
        user=user,
        is_active=True,
    ).select_related("role").first()
    if membership is None:
        raise ValidationError("User does not have an active membership in this tenant")
    return membership


def require_same_tenant(*, tenant, **objects):
    """Reject a domain operation when any tenant-owned object crosses boundaries."""
    mismatched = [name for name, value in objects.items() if value.tenant_id != tenant.id]
    if mismatched:
        names = ", ".join(sorted(mismatched))
        raise ValidationError(f"Objects belong to a different tenant: {names}")