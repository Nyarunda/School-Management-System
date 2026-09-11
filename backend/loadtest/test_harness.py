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


if __name__ == "__main__":
    unittest.main()
