import logging
import uuid

from .base import NotificationGateway

logger = logging.getLogger(__name__)


class StubEmailGateway(NotificationGateway):
    """Proves the outbox -> gateway -> delivery-attempt pipeline end-to-end
    without a real email provider (its own future milestone).
    """

    def send(self, *, tenant, recipient, subject, body, sender_id, context):
        logger.info("EMAIL (stub): to %s -- subject=%r -- %s", recipient, subject, body)
        return f"stub-email-{uuid.uuid4()}"
