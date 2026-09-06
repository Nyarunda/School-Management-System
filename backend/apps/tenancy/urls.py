from django.urls import path

from .api import SessionView


urlpatterns = [path("", SessionView.as_view(), name="session")]