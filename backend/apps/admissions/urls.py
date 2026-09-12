from django.urls import path

from .api import ApplicationEnrollView

urlpatterns = [
    path("applications/<uuid:application_id>/enroll/", ApplicationEnrollView.as_view(), name="application-enroll"),
]
