from django.core.exceptions import ValidationError

from ..models import NotificationChannel, NotificationProviderConfig
from .base import NotificationGateway
from .email import StubEmailGateway
from .in_app import InAppGateway
from .sms import StubSMSGateway

_STUB_GATEWAYS = {
    NotificationChannel.SMS: StubSMSGateway(),
    NotificationChannel.EMAIL: StubEmailGateway(),
}
_IN_APP_GATEWAY = InAppGateway()


def resolve_gateway(*, tenant, channel):
    """IN_APP always resolves to the internal gateway -- it has no external
    provider (configure_provider rejects IN_APP outright). For SMS/EMAIL,
    an unconfigured tenant or an explicit provider="STUB" both resolve to
    the stub (keeping dev/tests working without setup); any other
    configured provider string is not yet implemented and raises a clear
    error rather than silently sending through the wrong channel -- setup
    is meant to actually choose behavior, not just be decorative.
    """
    if channel == NotificationChannel.IN_APP:
        return _IN_APP_GATEWAY
    config = NotificationProviderConfig.objects.filter(tenant=tenant, channel=channel, is_active=True).first()
    provider = config.provider if config is not None else "STUB"
    if provider == "STUB":
        return _STUB_GATEWAYS[channel]
    raise ValidationError(f"Unsupported notification provider for {channel}: {provider}")


__all__ = ["NotificationGateway", "resolve_gateway"]
