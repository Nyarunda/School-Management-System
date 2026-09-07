from django.urls import path

from .api import (
    ClassScheduleView,
    PeriodDetailView,
    PeriodListCreateView,
    TeacherScheduleView,
    TimetableEntryCreateView,
    TimetableEntryDetailView,
    TimetableEntryListView,
)

urlpatterns = [
    path("periods/", PeriodListCreateView.as_view(), name="timetable-period-list"),
    path("periods/<uuid:period_id>/", PeriodDetailView.as_view(), name="timetable-period-detail"),
    path("entries/", TimetableEntryListView.as_view(), name="timetable-entry-list"),
    path("entries/create/", TimetableEntryCreateView.as_view(), name="timetable-entry-create"),
    path("entries/<uuid:entry_id>/", TimetableEntryDetailView.as_view(), name="timetable-entry-detail"),
    path("classes/<uuid:class_group_id>/schedule/", ClassScheduleView.as_view(), name="timetable-class-schedule"),
    path("teachers/<uuid:teacher_id>/schedule/", TeacherScheduleView.as_view(), name="timetable-teacher-schedule"),
]
