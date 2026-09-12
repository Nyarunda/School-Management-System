from ..models import UserNotification
from .base import NotificationGateway


class InAppGateway(NotificationGateway):
    """Instead of an external call, writes a UserNotification row for
    outbox.recipient_user. Idempotent via source_notification's OneToOne:
    a worker that creates the row and then crashes before the outbox is
    marked processed retries into a no-op (get_or_create returns the
    existing row) rather than a duplicate in-app notification.
    """

    def send(self, *, outbox):
        notification, _ = UserNotification.objects.get_or_create(
            tenant=outbox.tenant,
            source_notification=outbox,
            defaults={
                "user": outbox.recipient_user,
                "title": outbox.context.get("subject", ""),
                "message": outbox.context.get("body", ""),
                "resource_type": outbox.context.get("resource_type", ""),
                "resource_id": str(outbox.context.get("resource_id", "")),
            },
        )
        return f"in-app-{notification.id}"
