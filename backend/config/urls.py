from django.urls import include, path


urlpatterns = [
	path("api/v1/session/", include("apps.tenancy.urls")),
	path("api/v1/students/", include("apps.students.urls")),
]
urlpatterns += [path("api/v1/finance/", include("apps.finance.urls"))]
urlpatterns += [path("api/v1/attendance/", include("apps.attendance.urls"))]
urlpatterns += [path("api/v1/assessments/", include("apps.assessments.urls"))]