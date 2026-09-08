from django.urls import include, path


urlpatterns = [
	path("api/v1/session/", include("apps.tenancy.urls")),
	path("api/v1/students/", include("apps.students.urls")),
]
urlpatterns += [path("api/v1/finance/", include("apps.finance.urls"))]
urlpatterns += [path("api/v1/attendance/", include("apps.attendance.urls"))]
urlpatterns += [path("api/v1/assessments/", include("apps.assessments.urls"))]
urlpatterns += [path("api/v1/timetable/", include("apps.timetable.urls"))]
urlpatterns += [path("api/v1/staff/", include("apps.staff.urls"))]
urlpatterns += [path("api/v1/leave/", include("apps.leave.urls"))]
urlpatterns += [path("api/v1/notifications/", include("apps.notifications.urls"))]
urlpatterns += [path("api/v1/documents/", include("apps.documents.urls"))]
urlpatterns += [path("api/v1/reports/", include("apps.reporting.urls"))]
urlpatterns += [path("api/v1/platform/", include("apps.platform.urls"))]