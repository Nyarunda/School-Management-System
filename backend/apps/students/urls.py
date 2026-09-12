from django.urls import path

from .api import (
    StudentDocumentDeleteView,
    StudentDocumentDownloadView,
    StudentDocumentListCreateView,
    StudentListView,
)

urlpatterns = [
    path("", StudentListView.as_view(), name="student-list"),
    path("<uuid:student_id>/documents/", StudentDocumentListCreateView.as_view(), name="student-documents"),
    path("<uuid:student_id>/documents/<uuid:document_id>/download/", StudentDocumentDownloadView.as_view(), name="student-document-download"),
    path("<uuid:student_id>/documents/<uuid:document_id>/", StudentDocumentDeleteView.as_view(), name="student-document-delete"),
]
