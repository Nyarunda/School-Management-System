from django.urls import path

from .api import (
    ModuleCatalogueView,
    PlatformAuditEventListView,
    SubscriptionPlanDetailView,
    SubscriptionPlanListCreateView,
    TenantAuditEventListView,
    TenantModuleOverrideDetailView,
    TenantModuleOverrideListView,
    TenantProvisionView,
    TenantSubscriptionView,
)

urlpatterns = [
    path("modules/", ModuleCatalogueView.as_view(), name="platform-modules"),
    path("plans/", SubscriptionPlanListCreateView.as_view(), name="platform-plans"),
    path("plans/<uuid:plan_id>/", SubscriptionPlanDetailView.as_view(), name="platform-plan-detail"),
    path("tenants/", TenantProvisionView.as_view(), name="platform-tenant-provision"),
    path("tenants/<uuid:tenant_id>/subscription/", TenantSubscriptionView.as_view(), name="platform-tenant-subscription"),
    path("tenants/<uuid:tenant_id>/overrides/", TenantModuleOverrideListView.as_view(), name="platform-tenant-overrides"),
    path(
        "tenants/<uuid:tenant_id>/overrides/<str:module_code>/",
        TenantModuleOverrideDetailView.as_view(), name="platform-tenant-override-detail",
    ),
    path("audit-events/", PlatformAuditEventListView.as_view(), name="platform-audit-events"),
    path(
        "tenants/<uuid:tenant_id>/audit-events/",
        TenantAuditEventListView.as_view(), name="platform-tenant-audit-events",
    ),
]
