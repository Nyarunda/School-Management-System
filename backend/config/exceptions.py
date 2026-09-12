"""Translate domain validation errors at the DRF boundary."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        # Preserve field errors and the existing finance detail-list response.
        detail = exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages}
        exc = ValidationError(detail)
    # Delegate to DRF so its status handling and transaction rollback still run.
    return drf_exception_handler(exc, context)
