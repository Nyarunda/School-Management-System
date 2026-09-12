from django.urls import path

from .api import (
    CommunicationChannelDetailView,
    CommunicationSetupView,
    NotificationOutboxDeliveriesView,
    NotificationOutboxDetailView,
    NotificationOutboxListView,
    NotificationOutboxRetryView,
    NotificationProviderListCreateView,
    NotificationRuleDetailView,
    NotificationRuleListCreateView,
    NotificationTemplateDetailView,
    NotificationTemplateListCreateView,
    UserNotificationInboxView,
    UserNotificationMarkReadView,
)

urlpatterns = [
    path("setup/", CommunicationSetupView.as_view(), name="notifications-setup"),
    path("channels/<str:channel>/", CommunicationChannelDetailView.as_view(), name="notifications-channel-detail"),
    path("providers/", NotificationProviderListCreateView.as_view(), name="notifications-provider-list"),
    path("templates/", NotificationTemplateListCreateView.as_view(), name="notifications-template-list"),
    path("templates/<uuid:template_id>/", NotificationTemplateDetailView.as_view(), name="notifications-template-detail"),
    path("rules/", NotificationRuleListCreateView.as_view(), name="notifications-rule-list"),
    path("rules/<uuid:rule_id>/", NotificationRuleDetailView.as_view(), name="notifications-rule-detail"),
    path("outbox/", NotificationOutboxListView.as_view(), name="notifications-outbox-list"),
    path("outbox/<uuid:outbox_id>/", NotificationOutboxDetailView.as_view(), name="notifications-outbox-detail"),
    path("outbox/<uuid:outbox_id>/retry/", NotificationOutboxRetryView.as_view(), name="notifications-outbox-retry"),
    path("outbox/<uuid:outbox_id>/deliveries/", NotificationOutboxDeliveriesView.as_view(), name="notifications-outbox-deliveries"),
    path("inbox/", UserNotificationInboxView.as_view(), name="notifications-inbox-list"),
    path("inbox/<uuid:notification_id>/read/", UserNotificationMarkReadView.as_view(), name="notifications-inbox-read"),
]
