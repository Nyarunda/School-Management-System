class NotificationGateway:
    """A channel's send seam. Implementations raise on failure -- the caller
    (tasks.dispatch_pending_notifications) translates that into the outbox's
    existing retry/backoff/dead-letter handling, unchanged from before this
    milestone. Takes the claimed NotificationOutbox row itself (rather than
    unpacked fields) since IN_APP delivery needs the row for its own
    idempotent get_or_create against UserNotification.source_notification.

    Delivery guarantee, stated plainly rather than implied: outbox
    processing itself is at-least-once (a crash between a successful send
    and mark_processed() retries the send). outbox.idempotency_key is
    available so a real provider that supports idempotent submission
    (future work) can close that gap -- the stub gateways ignore it. IN_APP
    is the one channel this milestone makes effectively exactly-once, via
    UserNotification's own idempotent get_or_create, not via anything at
    this interface level.
    """

    def send(self, *, outbox) -> str:
        """Returns a provider_reference string, or raises on failure."""
        raise NotImplementedError
