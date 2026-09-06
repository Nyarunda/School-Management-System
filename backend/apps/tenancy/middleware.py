from .context import active_tenant
from django.core.exceptions import ValidationError
from .services import require_membership


class TenantResolutionMiddleware:
    """Resolve the active tenant from a slug only after membership is verified."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = None
        tenant_slug = request.headers.get("X-Tenant-Slug")
        if tenant_slug and request.user.is_authenticated:
            try:
                tenant = require_membership(user=request.user, tenant_slug=tenant_slug).tenant
            except ValidationError:
                tenant = None
            if tenant is not None:
                token = active_tenant.set(tenant)
                request.active_tenant = tenant

        try:
            return self.get_response(request)
        finally:
            if token is not None:
                active_tenant.reset(token)
