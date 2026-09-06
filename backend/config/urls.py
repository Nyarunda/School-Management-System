from django.urls import include, path


urlpatterns = [path("api/v1/students/", include("apps.students.urls"))]