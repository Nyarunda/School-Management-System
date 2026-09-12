from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.platform.services import require_module_enabled
from apps.tenancy.services import require_permission

from .models import DocumentSetup
from .services import configure_document_setup


def resolve_documents_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
        require_module_enabled(tenant=membership.tenant, module_code="documents")
        return membership.tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class DocumentSetupSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentSetup
        fields = ["default_retention_days"]


class DocumentSetupView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = resolve_documents_tenant(request, "documents.setup.view")
        setup = DocumentSetup.objects.filter(tenant=tenant).first()
        if setup is None:
            return Response({"default_retention_days": None})
        return Response(DocumentSetupSerializer(setup).data)

    def patch(self, request):
        tenant = resolve_documents_tenant(request, "documents.setup.manage")
        serializer = DocumentSetupSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            setup = configure_document_setup(user=request.user, tenant=tenant, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(DocumentSetupSerializer(setup).data)
