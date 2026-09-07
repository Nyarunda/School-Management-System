from django.urls import path

from .api import (
    EmployeeDetailView,
    EmployeeDocumentListCreateView,
    EmployeeListCreateView,
    EmployeeQualificationListCreateView,
    EmployeeStatusView,
    EmployeeUserLinkView,
)

urlpatterns = [
    path("employees/", EmployeeListCreateView.as_view(), name="staff-employee-list"),
    path("employees/<uuid:employee_id>/", EmployeeDetailView.as_view(), name="staff-employee-detail"),
    path("employees/<uuid:employee_id>/status/", EmployeeStatusView.as_view(), name="staff-employee-status"),
    path("employees/<uuid:employee_id>/documents/", EmployeeDocumentListCreateView.as_view(), name="staff-employee-documents"),
    path("employees/<uuid:employee_id>/qualifications/", EmployeeQualificationListCreateView.as_view(), name="staff-employee-qualifications"),
    path("employees/<uuid:employee_id>/user-link/", EmployeeUserLinkView.as_view(), name="staff-employee-user-link"),
]
