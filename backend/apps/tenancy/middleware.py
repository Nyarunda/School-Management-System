from .context import active_tenant
from .models import Membership, Tenant


class TenantResolutionMiddleware:
    """Resolve the active tenant from a slug only after membership is verified."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = None
        tenant_slug = request.headers.get("X-Tenant-Slug")
        if tenant_slug and request.user.is_authenticated:
            tenant = Tenant.objects.filter(slug=tenant_slug, is_active=True).first()
            if tenant and Membership.objects.filter(
                tenant=tenant,
                user=request.user,
                is_active=True,
            ).exists():
                token = active_tenant.set(tenant)
                request.active_tenant = tenant

        try:
            return self.get_response(request)
        finally:
            if token is not None:
                active_tenant.reset(token)