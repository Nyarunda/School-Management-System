from django.core.exceptions import ValidationError

from apps.tenancy.models import User

from ..models import UserNotification
from .base import NotificationGateway


class InAppGateway(NotificationGateway):
    """Instead of an external call, writes a UserNotification row for the
    User identified by `recipient` (str(user.id), set by
    catalogue._resolve_employee_recipients for IN_APP rows). A missing/
    unparsable recipient is a genuine data problem, not a transient
    failure -- it still goes through the outbox's normal retry/backoff
    path and eventually dead-letters, same as any other gateway failure.
    """

    def send(self, *, tenant, recipient, subject, body, sender_id, context):
        try:
            user = User.objects.get(pk=recipient)
        except (User.DoesNotExist, ValueError, ValidationError) as error:
            raise ValidationError(f"No user found for in-app recipient {recipient!r}") from error
        UserNotification.objects.create(
            tenant=tenant,
            user=user,
            title=subject or context.get("title", ""),
            message=body,
            resource_type=context.get("resource_type", ""),
            resource_id=str(context.get("resource_id", "")),
        )
        return f"in-app-{user.id}"
