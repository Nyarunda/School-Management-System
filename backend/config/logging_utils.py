import json
import logging
from contextvars import ContextVar

current_request_id = ContextVar("current_request_id", default=None)


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = current_request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON line per record. `event`/`method`/`path`/`status`/`duration_ms`
    are included only when passed via `extra=` on the log call (e.g. the
    RequestIdMiddleware's per-request completion log) -- deliberately not
    tenant_id/user_id, which needs its own privacy/security consideration.
    """

    EXTRA_FIELDS = ("event", "method", "path", "status", "duration_ms")

    def format(self, record):
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        for field in self.EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)
