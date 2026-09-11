"""Fast, HTTP-free regression check for run_ramp's per-tenant pacing.

No Django, no network, no docker compose -- consistent with harness.py's
own "plain script" design. Monkeypatches send_c2b_confirmation to a
counting stub and runs a short, high-rate ramp step, then asserts
measured per-tenant request counts are actually proportional to weight,
not just that the function completes without error.

Run directly: python -m loadtest.test_harness (from backend/), or
python -m unittest loadtest.test_harness.
"""
import asyncio
import unittest
from unittest import mock

from loadtest import harness


class WeightedRampTests(unittest.TestCase):
    def test_noisy_tenant_receives_roughly_ten_times_the_requests(self):
        manifest = {
            "tenants": [
                {"slug": "noisy", "is_noisy": True, "students": [{"admission_number": "A"}]},
                {"slug": "normal-1", "is_noisy": False, "students": [{"admission_number": "B"}]},
                {"slug": "normal-2", "is_noisy": False, "students": [{"admission_number": "C"}]},
            ]
        }
        counts = {"noisy": 0, "normal-1": 0, "normal-2": 0}

        async def fake_send(client, base_url, tenant_entry, *, run_id, counter, amount, recorder, phase,
                             hot_invoice_fraction, duplicate_storm, storm_size, storm_counter):
            counts[tenant_entry["slug"]] += 1
            return storm_counter

        async def run():
            with mock.patch.object(harness, "send_c2b_confirmation", fake_send):
                await harness.run_ramp(
                    client=None, base_url="unused", manifest=manifest, run_id="test", ramp=[12000],
                    step_duration=2.0, amount="1", recorder=harness.Recorder(), phase="test",
                    hot_invoice_fraction=0.0, duplicate_storm=False, storm_size=20, concurrency_limit=1000,
                )

        asyncio.run(run())

        self.assertGreater(counts["noisy"], 50, "too few samples to judge the ratio reliably")
        self.assertGreater(counts["normal-1"], 5, "too few samples to judge the ratio reliably")
        self.assertGreater(counts["normal-2"], 5, "too few samples to judge the ratio reliably")
        ratio_1 = counts["noisy"] / counts["normal-1"]
        ratio_2 = counts["noisy"] / counts["normal-2"]
        # weight is 10:1:1 -- generous slack for real-clock scheduling
        # jitter over a short window; this checks "genuinely proportional",
        # not an exact ratio. The bug this guards against produced a ratio
        # near 1, not near 10, so this band clearly distinguishes the two.
        self.assertTrue(6 <= ratio_1 <= 15, f"noisy:normal-1 ratio was {ratio_1}, expected ~10 ({counts})")
        self.assertTrue(6 <= ratio_2 <= 15, f"noisy:normal-2 ratio was {ratio_2}, expected ~10 ({counts})")


class TenantHeadersTests(unittest.TestCase):
    """Regression check for the auth fix: the harness used to send
    httpx.BasicAuth, which config.settings' DEFAULT_AUTHENTICATION_CLASSES
    (TokenAuthentication + SessionAuthentication, no BasicAuthentication)
    has never accepted -- every authenticated harness request was silently
    401ing. _tenant_headers replaces that with a real login-then-Token flow.
    """

    def test_logs_in_once_and_reuses_the_cached_token(self):
        harness._token_cache.clear()
        login_calls = []

        class FakeResponse:
            def __init__(self, token):
                self._token = token

            def raise_for_status(self):
                pass

            def json(self):
                return {"token": self._token}

        class FakeClient:
            async def post(self, url, *, json, timeout):
                login_calls.append((url, json["username"], json["password"]))
                return FakeResponse("tok-for-" + json["username"])

        tenant_entry = {"slug": "loadtest-0", "bursar_username": "loadtest-bursar-0", "bursar_password": "secret"}

        async def run():
            headers_1 = await harness._tenant_headers(FakeClient(), "http://base", tenant_entry)
            headers_2 = await harness._tenant_headers(FakeClient(), "http://base", tenant_entry)
            return headers_1, headers_2

        headers_1, headers_2 = asyncio.run(run())

        self.assertEqual(len(login_calls), 1, "should log in once and cache the token, not once per call")
        self.assertEqual(login_calls[0], ("http://base/api/v1/auth/login/", "loadtest-bursar-0", "secret"))
        self.assertEqual(headers_1, {"Authorization": "Token tok-for-loadtest-bursar-0", "X-Tenant-Slug": "loadtest-0"})
        self.assertEqual(headers_1, headers_2)


if __name__ == "__main__":
    unittest.main()
