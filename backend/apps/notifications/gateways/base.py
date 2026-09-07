class NotificationGateway:
    """A channel's send seam. Implementations raise on failure -- the caller
    (tasks.dispatch_pending_notifications) translates that into the outbox's
    existing retry/backoff/dead-letter handling, unchanged from before this
    milestone.
    """

    def send(self, *, tenant, recipient, subject, body, sender_id, context):
        raise NotImplementedError
