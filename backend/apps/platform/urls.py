from django.urls import path

from .api import (
    ModuleCatalogueView,
    SubscriptionPlanDetailView,
    SubscriptionPlanListCreateView,
    TenantModuleOverrideDetailView,
    TenantModuleOverrideListView,
    TenantSubscriptionView,
)

urlpatterns = [
    path("modules/", ModuleCatalogueView.as_view(), name="platform-modules"),
    path("plans/", SubscriptionPlanListCreateView.as_view(), name="platform-plans"),
    path("plans/<uuid:plan_id>/", SubscriptionPlanDetailView.as_view(), name="platform-plan-detail"),
    path("tenants/<uuid:tenant_id>/subscription/", TenantSubscriptionView.as_view(), name="platform-tenant-subscription"),
    path("tenants/<uuid:tenant_id>/overrides/", TenantModuleOverrideListView.as_view(), name="platform-tenant-overrides"),
    path(
        "tenants/<uuid:tenant_id>/overrides/<str:module_code>/",
        TenantModuleOverrideDetailView.as_view(), name="platform-tenant-override-detail",
    ),
]
