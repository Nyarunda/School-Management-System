from decimal import InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import path
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from apps.tenancy.models import Tenant
from .exceptions import exception_handler


class ValidationFailureView(APIView):
    authentication_classes = []
    permission_classes = []
    error = None

    def get(self, request):
        raise self.error


class RollbackView(ValidationFailureView):
    def post(self, request):
        Tenant.objects.create(name="Rollback", slug="rollback")
        raise DjangoValidationError("Rejected")


urlpatterns = [path("validation-rollback/", RollbackView.as_view())]


class ExceptionHandlerTests(SimpleTestCase):
    def response_for(self, error):
        return ValidationFailureView.as_view(error=error)(APIRequestFactory().get("/"))

    def test_django_message_and_list_errors_keep_detail_envelope(self):
        for error, expected in (
            (DjangoValidationError("Invalid"), ["Invalid"]),
            (DjangoValidationError(["First", "Second"]), ["First", "Second"]),
            (DjangoValidationError("Invalid %(value)s", params={"value": "amount"}), ["Invalid amount"]),
        ):
            with self.subTest(expected=expected):
                response = self.response_for(error)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {"detail": expected})

    def test_django_field_errors_preserve_field_names(self):
        response = self.response_for(DjangoValidationError({"amount": ["Must be positive"]}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"amount": ["Must be positive"]})

    def test_drf_validation_errors_are_unchanged(self):
        response = self.response_for(ValidationError({"amount": ["Invalid"]}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"amount": ["Invalid"]})

    def test_explicit_forbidden_and_not_found_remain_distinct(self):
        for error, expected in ((PermissionDenied("Denied"), 403), (Http404("Missing"), 404)):
            with self.subTest(status=expected):
                self.assertEqual(self.response_for(error).status_code, expected)

    def test_unexpected_errors_are_not_hidden_as_client_errors(self):
        for error in (IntegrityError("constraint"), InvalidOperation(), RuntimeError("bug")):
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(exception_handler(error, {}))


@override_settings(ROOT_URLCONF=__name__)
class ExceptionRollbackTests(TestCase):
    def test_validation_response_rolls_back_atomic_request(self):
        original = connection.settings_dict.get("ATOMIC_REQUESTS", False)
        connection.settings_dict["ATOMIC_REQUESTS"] = True
        try:
            with transaction.atomic():
                response = APIClient().post("/validation-rollback/")
                self.assertEqual(response.status_code, 400)
            self.assertFalse(Tenant.objects.filter(slug="rollback").exists())
        finally:
            connection.settings_dict["ATOMIC_REQUESTS"] = original
