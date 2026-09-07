import logging
import uuid

from .base import NotificationGateway

logger = logging.getLogger(__name__)


class StubSMSGateway(NotificationGateway):
    """Proves the outbox -> gateway -> delivery-attempt pipeline end-to-end
    without a real SMS provider (its own future milestone, mirroring
    M-Pesa's). Returns a fake provider_reference so NotificationDeliveryAttempt
    rows look exactly like a real provider's would.
    """

    def send(self, *, tenant, recipient, subject, body, sender_id, context):
        logger.info("SMS (stub): to %s -- %s", recipient, body)
        return f"stub-sms-{uuid.uuid4()}"
