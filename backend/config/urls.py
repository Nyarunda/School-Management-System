from django.urls import include, path

from .health import liveness, readiness

urlpatterns = [
	path("healthz/", liveness, name="liveness"),
	path("readyz/", readiness, name="readiness"),
	path("api/v1/session/", include("apps.tenancy.urls")),
	path("api/v1/tenancy/", include("apps.tenancy.admin_urls")),
	path("api/v1/auth/", include("apps.tenancy.auth_urls")),
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
urlpatterns += [path("api/v1/admissions/", include("apps.admissions.urls"))]
urlpatterns += [path("api/v1/academics/", include("apps.academics.urls"))]