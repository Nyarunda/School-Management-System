from django.urls import path

from .api import (
    ReportCatalogueListView,
    ReportExportDownloadView,
    ReportExportJobDetailView,
    ReportExportRequestView,
    ReportPreviewView,
)

urlpatterns = [
    path("catalogue/", ReportCatalogueListView.as_view(), name="reports-catalogue"),
    path("exports/<uuid:job_id>/", ReportExportJobDetailView.as_view(), name="reports-export-detail"),
    path("exports/<uuid:job_id>/download/", ReportExportDownloadView.as_view(), name="reports-export-download"),
    path("<str:report_code>/preview/", ReportPreviewView.as_view(), name="reports-preview"),
    path("<str:report_code>/export/", ReportExportRequestView.as_view(), name="reports-export-request"),
]
