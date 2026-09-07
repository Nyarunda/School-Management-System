from django.urls import path

from .api import DocumentSetupView

urlpatterns = [
    path("setup/", DocumentSetupView.as_view(), name="documents-setup"),
]
