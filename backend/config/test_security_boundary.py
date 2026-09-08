"""Milestone 22.1 -- turns the one-time audit (113 DRF views inspected,
every one explicitly declares permission_classes, only 3 are AllowAny) into
a permanent regression test using Django's actual URL resolver, so any
future view that becomes public by accident (or omits permission_classes
and falls back to DRF's AllowAny default) fails CI instead of shipping.
"""
from django.conf import settings
from django.test import SimpleTestCase
from django.urls import get_resolver
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

EXPECTED_PUBLIC_VIEWS = {
    "MpesaC2BValidationView",
    "MpesaC2BConfirmationView",
    "MpesaStkCallbackView",
    "LoginView",
    "InviteAcceptView",
}


def _iter_view_classes(url_patterns):
    for pattern in url_patterns:
        nested = getattr(pattern, "url_patterns", None)
        if nested is not None:
            yield from _iter_view_classes(nested)
            continue
        view_class = getattr(pattern.callback, "view_class", None)
        if view_class is not None and issubclass(view_class, APIView):
            yield view_class


class PublicSurfaceRegressionTests(SimpleTestCase):
    def test_every_view_declares_permission_classes(self):
        missing = [
            view_class.__name__ for view_class in _iter_view_classes(get_resolver().url_patterns)
            if "permission_classes" not in view_class.__dict__
        ]
        self.assertEqual(missing, [])

    def test_only_the_known_mpesa_webhooks_allow_anonymous_access(self):
        public_views = {
            view_class.__name__ for view_class in _iter_view_classes(get_resolver().url_patterns)
            if AllowAny in getattr(view_class, "permission_classes", [])
        }
        self.assertEqual(public_views, EXPECTED_PUBLIC_VIEWS)

    def test_xframeoptionsmiddleware_is_installed(self):
        """X_FRAME_OPTIONS in settings.py has no effect at all unless this
        middleware is also present -- caught by `manage.py check --deploy`
        (security.W002) during Milestone 22.1 verification; this is what
        keeps it caught permanently rather than needing a human to remember
        to run that command.
        """
        self.assertIn("django.middleware.clickjacking.XFrameOptionsMiddleware", settings.MIDDLEWARE)
