import logging
import time
import uuid

from .logging_utils import current_request_id

request_logger = logging.getLogger("http.request")


class RequestIdMiddleware:
    """Generates a fresh request id for every request (never trusts an
    inbound X-Request-ID -- that would require reasoning about which
    proxies are allowed to set it), makes it available to every log record
    emitted during the request via the current_request_id contextvar, and
    logs one completion line with method/path/status/duration_ms so
    observability covers latency, not just errors. Only request.path is
    logged, never the query string, since it can carry sensitive values.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = str(uuid.uuid4())
        request.request_id = request_id
        token = current_request_id.set(request_id)
        start = time.monotonic()
        try:
            response = self.get_response(request)
        finally:
            current_request_id.reset(token)
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        response["X-Request-ID"] = request_id
        request_logger.info(
            "%s %s -> %s (%.1fms)", request.method, request.path, response.status_code, duration_ms,
            extra={
                "event": "http_request", "method": request.method, "path": request.path,
                "status": response.status_code, "duration_ms": duration_ms,
            },
        )
        return response
