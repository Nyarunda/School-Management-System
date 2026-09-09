from django.core.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.platform.services import get_enabled_modules

from .models import Membership
from .services import require_membership


class SessionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        memberships = Membership.objects.filter(
            user=request.user,
            tenant__is_active=True,
            is_active=True,
        ).select_related("tenant", "role")
        requested_slug = request.headers.get("X-Tenant-Slug")
        active = None
        if requested_slug:
            try:
                active = require_membership(user=request.user, tenant_slug=requested_slug)
            except ValidationError as error:
                raise PermissionDenied(error.messages) from error
        elif memberships:
            active = memberships[0]
        return Response({
            "user": {
                "id": str(request.user.id),
                "username": request.user.get_username(),
                "name": request.user.get_full_name() or request.user.get_username(),
                "is_platform_admin": request.user.is_superuser,
            },
            "active_tenant": {
                "id": str(active.tenant.id),
                "name": active.tenant.name,
                "slug": active.tenant.slug,
                "enabled_modules": sorted(get_enabled_modules(active.tenant)),
            } if active else None,
            "memberships": [{
                "tenant": {"id": str(item.tenant.id), "name": item.tenant.name, "slug": item.tenant.slug},
                "role": item.role.name,
                "permissions": item.role.permissions,
            } for item in memberships],
            "permissions": active.role.permissions if active else [],
        })