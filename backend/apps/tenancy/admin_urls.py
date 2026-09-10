from django.urls import path

from .admin_api import (
    CampusListView,
    MembershipActivateView,
    MembershipDeactivateView,
    MembershipDetailView,
    MembershipListView,
    PermissionCatalogueView,
    RoleDetailView,
    RoleListCreateView,
    UserInviteView,
)

urlpatterns = [
    path("permissions/", PermissionCatalogueView.as_view(), name="tenancy-permissions"),
    path("campuses/", CampusListView.as_view(), name="tenancy-campuses"),
    path("roles/", RoleListCreateView.as_view(), name="tenancy-roles"),
    path("roles/<int:role_id>/", RoleDetailView.as_view(), name="tenancy-role-detail"),
    path("memberships/", MembershipListView.as_view(), name="tenancy-memberships"),
    path("memberships/<int:membership_id>/", MembershipDetailView.as_view(), name="tenancy-membership-detail"),
    path("memberships/<int:membership_id>/activate/", MembershipActivateView.as_view(), name="tenancy-membership-activate"),
    path("memberships/<int:membership_id>/deactivate/", MembershipDeactivateView.as_view(), name="tenancy-membership-deactivate"),
    path("users/invite/", UserInviteView.as_view(), name="tenancy-user-invite"),
]
