import logging
import uuid

from .base import NotificationGateway

logger = logging.getLogger(__name__)


class StubEmailGateway(NotificationGateway):
    """Proves the outbox -> gateway -> delivery-attempt pipeline end-to-end
    without a real email provider (its own future milestone).
    """

    def send(self, *, outbox):
        logger.info(
            "EMAIL (stub): to %s -- subject=%r -- %s", outbox.recipient, outbox.context.get("subject", ""), outbox.context.get("body", ""),
        )
        return f"stub-email-{uuid.uuid4()}"
