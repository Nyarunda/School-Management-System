from django.urls import path

from .api import AcademicLevelListView, AcademicYearListView, ClassGroupListView, TermListView

urlpatterns = [
    path("academic-years/", AcademicYearListView.as_view(), name="academic-year-list"),
    path("academic-levels/", AcademicLevelListView.as_view(), name="academic-level-list"),
    path("class-groups/", ClassGroupListView.as_view(), name="class-group-list"),
    path("terms/", TermListView.as_view(), name="term-list"),
]
