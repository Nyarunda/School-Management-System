from django.urls import path

from .api import AcademicLevelListView, AcademicYearListView

urlpatterns = [
    path("academic-years/", AcademicYearListView.as_view(), name="academic-year-list"),
    path("academic-levels/", AcademicLevelListView.as_view(), name="academic-level-list"),
]
