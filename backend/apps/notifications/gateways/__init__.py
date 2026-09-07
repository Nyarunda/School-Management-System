from ..models import NotificationChannel
from .base import NotificationGateway
from .email import StubEmailGateway
from .in_app import InAppGateway
from .sms import StubSMSGateway

_GATEWAYS = {
    NotificationChannel.SMS: StubSMSGateway(),
    NotificationChannel.EMAIL: StubEmailGateway(),
    NotificationChannel.IN_APP: InAppGateway(),
}


def resolve_gateway(*, channel):
    """SMS/EMAIL currently always resolve to their stub regardless of a
    tenant's NotificationProviderConfig.provider -- real provider dispatch
    (Africa's Talking, Twilio, SMTP, SendGrid, ...) is a documented future
    extension; this milestone builds the seam, not every implementation.
    """
    return _GATEWAYS[channel]


__all__ = ["NotificationGateway", "resolve_gateway"]
