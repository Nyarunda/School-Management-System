from django.urls import path

from .api import (
    SessionDetailView,
    SessionListView,
    SessionOpenView,
    SessionRecordsView,
    StudentAttendanceRecordListView,
    StudentAttendanceSummaryView,
)

urlpatterns = [
    path("sessions/", SessionListView.as_view(), name="attendance-session-list"),
    path("sessions/open/", SessionOpenView.as_view(), name="attendance-session-open"),
    path("sessions/<uuid:session_id>/", SessionDetailView.as_view(), name="attendance-session-detail"),
    path("sessions/<uuid:session_id>/records/", SessionRecordsView.as_view(), name="attendance-session-records"),
    path("students/<uuid:student_id>/summary/", StudentAttendanceSummaryView.as_view(), name="attendance-student-summary"),
    path("students/<uuid:student_id>/records/", StudentAttendanceRecordListView.as_view(), name="attendance-student-records"),
]
