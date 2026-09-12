from django.urls import path

from .api import StudentListView


urlpatterns = [path("", StudentListView.as_view(), name="student-list")]