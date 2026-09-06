"""A stand-in for Safaricom's Daraja sandbox, for Milestone 5E load runs.

STK push initiation makes a real outbound call via MpesaClient -- infeasible
(and against ToS) to point at the real internet at load-test volumes, and no
real sandbox can absorb thousands of req/min anyway. MpesaClient's
SANDBOX_BASE_URL is overridable via MPESA_SANDBOX_BASE_URL specifically so a
load run can point it here instead (see apps/finance/mpesa_client.py).

The one property that actually matters is generating a FRESH
MerchantRequestID/CheckoutRequestID per call, not a single fixed constant --
a naive fixed-ID stub would immediately collide with 5C.1's
unique_stk_checkout_request_per_tenant/unique_stk_receipt_per_tenant
constraints on the second concurrent STK push. Deeper request-body-derived
idempotency replay is deliberately not implemented here: our own
initiate_stk_push() already deduplicates by its own idempotency_key before
ever calling this client, so a genuine call to this stub always represents
a genuinely new attempt in our system -- replicating provider-side
idempotency in the stub would test a property the stub can't actually
violate.

Stdlib-only (no new dependency for something this small), run via
`python -m loadtest.fake_daraja` inside the load-test compose stack.
"""
import json
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.getenv("FAKE_DARAJA_PORT", "9999"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # quiet -- this runs at load-test volumes

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/oauth/v1/generate"):
            self._respond(200, {"access_token": "loadtest-fake-token", "expires_in": "3599"})
            return
        self._respond(404, {"error": "not found"})

    def do_POST(self):
        payload = self._read_json()
        if self.path == "/mpesa/stkpush/v1/processrequest":
            self._respond(200, {
                "MerchantRequestID": f"LT-MERCHANT-{uuid.uuid4()}",
                "CheckoutRequestID": f"LT-CHECKOUT-{uuid.uuid4()}",
                "ResponseCode": "0",
                "ResponseDescription": "Success. Request accepted for processing",
                "CustomerMessage": "Success. Request accepted for processing",
            })
            return
        if self.path == "/mpesa/stkpushquery/v1/query":
            self._respond(200, {
                "CheckoutRequestID": payload.get("CheckoutRequestID", ""),
                "ResultCode": "0",
                "ResultDesc": "The service request is processed successfully.",
            })
            return
        self._respond(404, {"error": "not found"})


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"fake-daraja listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
