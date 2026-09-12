import json
import logging
import uuid

from django.test import SimpleTestCase, TestCase

from .logging_utils import JsonFormatter, RequestIdFilter, current_request_id


def _record(name="test.logger", msg="hello", args=()):
    return logging.LogRecord(
        name=name, level=logging.INFO, pathname=__file__, lineno=1, msg=msg, args=args, exc_info=None,
    )


class JsonFormatterTests(SimpleTestCase):
    def test_formats_valid_json_with_expected_keys(self):
        record = _record(msg="hello %s", args=("world",))
        record.request_id = "abc-123"
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["message"], "hello world")
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "test.logger")
        self.assertEqual(payload["request_id"], "abc-123")
        self.assertIn("timestamp", payload)

    def test_includes_extra_fields_when_present(self):
        record = _record(name="http.request", msg="GET /x -> 200")
        record.request_id = None
        record.event = "http_request"
        record.method = "GET"
        record.path = "/x"
        record.status = 200
        record.duration_ms = 12.3
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["event"], "http_request")
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["path"], "/x")
        self.assertEqual(payload["status"], 200)
        self.assertEqual(payload["duration_ms"], 12.3)

    def test_omits_extra_fields_when_absent(self):
        record = _record()
        record.request_id = None
        payload = json.loads(JsonFormatter().format(record))
        for field in JsonFormatter.EXTRA_FIELDS:
            self.assertNotIn(field, payload)


class RequestIdFilterTests(SimpleTestCase):
    def test_yields_none_outside_a_request_context(self):
        record = _record()
        RequestIdFilter().filter(record)
        self.assertIsNone(record.request_id)

    def test_picks_up_the_contextvar_value(self):
        token = current_request_id.set("abc-123")
        try:
            record = _record()
            RequestIdFilter().filter(record)
            self.assertEqual(record.request_id, "abc-123")
        finally:
            current_request_id.reset(token)


class RequestIdMiddlewareTests(TestCase):
    def test_two_requests_get_different_ids(self):
        first = self.client.get("/healthz/")
        second = self.client.get("/healthz/")
        self.assertNotEqual(first["X-Request-ID"], second["X-Request-ID"])

    def test_response_id_is_a_valid_uuid(self):
        response = self.client.get("/healthz/")
        uuid.UUID(response["X-Request-ID"])

    def test_inbound_x_request_id_header_is_not_trusted(self):
        response = self.client.get("/healthz/", HTTP_X_REQUEST_ID="client-supplied")
        self.assertNotEqual(response["X-Request-ID"], "client-supplied")

    def test_emits_one_completion_log_line_per_request(self):
        with self.assertLogs("http.request", level="INFO") as captured:
            self.client.get("/healthz/")
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.event, "http_request")
        self.assertEqual(record.method, "GET")
        self.assertEqual(record.path, "/healthz/")
        self.assertEqual(record.status, 200)
        self.assertGreaterEqual(record.duration_ms, 0)
