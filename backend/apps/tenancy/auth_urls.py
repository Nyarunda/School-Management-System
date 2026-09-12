from django.urls import path

from .auth_api import InviteAcceptView, LoginView, LogoutView

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("invites/accept/", InviteAcceptView.as_view(), name="auth-invite-accept"),
]
