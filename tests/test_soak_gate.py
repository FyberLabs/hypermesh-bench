"""Known-host enroll plus AGX preflight. No live Jetson."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agx_facts import PASSING_FACTS, EnrolledHost  # noqa: E402
from agx_preflight import evaluate  # noqa: E402
from soak_gate import (  # noqa: E402
    KNOWN_DEVICE_ID,
    SoakRefused,
    assert_ready_for_soak,
)


class SoakGateTest(unittest.TestCase):
    def test_passing_facts_match_the_pins(self) -> None:
        report = evaluate(PASSING_FACTS)
        self.assertIs(report["ok"], True)

    def test_this_machine_is_refused(self) -> None:
        with self.assertRaises(SoakRefused) as caught:
            assert_ready_for_soak("dev-1")
        message = str(caught.exception)
        self.assertIn("AGX preflight failed closed", message)
        self.assertIn(KNOWN_DEVICE_ID, message)

    def test_enrolled_known_host_passes_when_preflight_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            enrolled = EnrolledHost(Path(tmp))
            try:
                ready = assert_ready_for_soak(None)
                self.assertEqual(ready["device_id"], KNOWN_DEVICE_ID)
                self.assertIs(ready["preflight"]["ok"], True)
                with self.assertRaises(SoakRefused):
                    assert_ready_for_soak("f6124d28-772c-4f1f-8e03-1a7a17724381")
            finally:
                enrolled.close()


if __name__ == "__main__":
    unittest.main()
